;; Line-locked tape loader with a border pattern.
;;
;; Every scanline gets one chunk of code exactly 224 T-states long, so after
;; one sync to the frame interrupt the loader stays locked to the beam for good,
;; with interrupts off. Each chunk samples the EAR bit at the same point of its
;; line and keeps the XOR with the previous sample (bit 6 set = an edge). What
;; the rest of the chunk does depends on where in the frame it is:
;;
;;   PRE      (scanline 15)       save the loader's alternate registers
;;   BORDER   (scanlines 16-63)   draw a row of the border pattern
;;   POST     (scanline 64)       restore them
;;   D lines                      decode two queued samples
;;   Z lines  (paper only)        nothing yet: free for effects
;;   I lines  (off the paper)     take one decoded byte off the queue and act
;;                                on it: a header field, or a byte to store
;;
;; Which of D, Z or I each line is comes from SCHEDULE, which
;; make_lineloader.py works out (it is in lineloader_gen.s, with the border).
;;
;; Contention. Nothing in a chunk on a paper line touches contended memory, and
;; its one I/O, the IN, happens in the horizontal blank, so no paper line is
;; ever held by the ULA. Loaded bytes are only written from I lines, and there
;; are only I lines above and below the paper.
;;
;; Bits. Every pulse - the time between two edges - is one bit: a short one
;; (1.5 lines) is a 0 and a long one (4.5 lines) a 1. A pulse is long if it
;; spans three samples or more. L keeps the run since the last edge as a shift
;; register ($3F after an edge, SLL'd for each sample with no edge), so its bit
;; 7 is set from the third sample; ADD HL,HL then moves that bit into H. H
;; starts each byte as 1, so the 1 falling out into the carry says a byte is
;; done. H = 0 is waiting for the sync: the leader's 0 bits leave it 0, and the
;; sync's 1 bit starts the first byte.
;;
;; Registers while loading:
;;   C   the last sample          DE  FIFO write pointer, and schedule index
;;   H   byte being shifted in    L   run since the last edge
;;   SP  FIFO read pointer        IX  queue write pointer (QUEUE page)
;;   IY  the next line's code (IYH = LINES_PAGE)
;;   B', C'  bytes left in the block    H', L'  where the next one goes
;;   D', E'  queue read pointer (QUEUE page)
;; The border rows use the alternate set for colours (C' = $FE), so PRE saves
;; it and POST puts it back.

;; ---------------------------------------------------------------------------
;; Padding to an exact number of T-states. Only at points where A and the
;; flags are dead: it uses NOP (4), CP 0 (7), LD A,I (9), JP $+3 (10) and, for
;; 40 or more, a loop on A (16n+2).

PAD             MACRO tstates
                IF (tstates) >= 40
                LD A,((tstates) - 22) / 16
                DEC A
                JR NZ,$-1
                PAD_SHORT (tstates) - 2 - 16 * (((tstates) - 22) / 16)
                ELSE
                PAD_SHORT tstates
                ENDIF
                ENDM

PAD_SHORT       MACRO tstates
                ASSERT (tstates) >= 0
                ASSERT (tstates) != 1 && (tstates) != 2 && (tstates) != 3 && (tstates) != 5 && (tstates) != 6
                ; (a true comparison is -1 in sjasmplus, so no arithmetic on them)
                IF (tstates) % 4 == 3
                CP 0
                NOPS ((tstates) - 7) / 4
                ENDIF
                IF (tstates) % 4 == 2
                JP $+3
                NOPS ((tstates) - 10) / 4
                ENDIF
                IF (tstates) % 4 == 1
                LD A,I
                NOPS ((tstates) - 9) / 4
                ENDIF
                IF (tstates) % 4 == 0
                NOPS (tstates) / 4
                ENDIF
                ENDM

;; A long wait, on BC (26 a loop).
DELAY           MACRO tstates
                LD BC,((tstates) - 5 - 30) / 26
                DEC BC
                LD A,B
                OR C
                JR NZ,$-3
                PAD (tstates) - 5 - 26 * (((tstates) - 5 - 30) / 26)
                ENDM

NOPS            MACRO count
                IF (count) > 0
                DUP count
                NOP
                EDUP
                ENDIF
                ENDM

;; The sample, at the start of every chunk (36 T-states, the IN first).
HEAD            MACRO
                IN A,($FE)          ; 11  the sample
                XOR C               ; 4   bit 6 set if it differs from the last
                LD (DE),A           ; 7   onto the FIFO
                INC DE              ; 6
                XOR C               ; 4   the sample again
                LD C,A              ; 4   is now the last one
                ENDM

;; The same for a line whose FIFO slot is fixed (PRE, the border, POST).
HEAD_AT         MACRO slot
                IN A,($FE)          ; 11
                XOR C               ; 4
                LD (slot),A         ; 13
                XOR C               ; 4
                LD C,A              ; 4
                ENDM

;; On to the next line: SCHEDULE sits $200 above the FIFO, so the entry for
;; the slot DE now points at is two pages up (39 T-states).
TAIL            MACRO
                INC D               ; 4
                INC D               ; 4
                LD A,(DE)           ; 7
                DEC D               ; 4
                DEC D               ; 4
                LD IYL,A            ; 8
                JP (IY)             ; 8
                ENDM

;; ===========================================================================
;; The I lines' byte handlers: one per thing a byte can be. Entered with the
;; alternate set in, the byte in A; each takes HANDLER_T T-states, its JP back
;; included, and says which handler the next byte goes to.

HANDLER_T       EQU 74
SYNC_T          EQU 47 + SYNC_ADJUST
;; PRE's line starts at 224 * 14 + LINE_START: LINE_START is where a line's IN
;; falls against its scanline, a few T-states into the horizontal blank.
PRE_T           EQU 224 * 14 + LINE_START
HANDLERS_PAGE   EQU HIGH $

NEXT_STATE      MACRO handler
                LD A,LOW handler    ; 7
                LD (STATE_JP+1),A   ; 13
                ENDM

;; The first byte after the sync must be the marker. Anything else means the
;; "sync" was noise: start waiting for one again, and drop what was queued.
H_MARK          CP LINE_MARKER          ; 7
                JP NZ,.bad              ; 10
                NEXT_STATE H_ADDR_HI    ; 20
                PAD HANDLER_T - 47
                JP I_DONE               ; 10
.bad            LD A,IXL                ; 8
                LD E,A                  ; 4   queue now empty
                EXX                     ; 4
                LD H,0                  ; 7   wait for a sync again
                EXX                     ; 4
                PAD HANDLER_T - 54
                JP I_DONE

;; A block's address is sent one page high; a page of 0 is the end marker.
H_ADDR_HI       DEC A                   ; 4
                LD H,A                  ; 4
                NEXT_STATE H_ADDR_LO    ; 20
                PAD HANDLER_T - 38
                JP I_DONE

H_ADDR_LO       LD L,A                  ; 4
                NEXT_STATE H_LEN_HI     ; 20
                PAD HANDLER_T - 34
                JP I_DONE

H_LEN_HI        LD B,A                  ; 4
                NEXT_STATE H_LEN_LO     ; 20
                PAD HANDLER_T - 34
                JP I_DONE

;; At the end marker the length is the entry address.
H_LEN_LO        LD C,A                  ; 4
                LD A,H                  ; 4
                OR A                    ; 4
                JP Z,EXIT               ; 10
                NEXT_STATE H_DATA       ; 20
                PAD HANDLER_T - 52
                JP I_DONE

H_DATA          LD (HL),A               ; 7
                INC HL                  ; 6
                DEC BC                  ; 6
                LD A,B                  ; 4
                OR C                    ; 4
                JP NZ,.more             ; 10
                NEXT_STATE H_ADDR_HI    ; 20
                PAD HANDLER_T - 67
                JP I_DONE
.more           PAD HANDLER_T - 47
                JP I_DONE

                ASSERT HIGH $ == HANDLERS_PAGE

;; Loaded: put back what BASIC and the ROM expect and jump in. The border stays
;; as it is, interrupts stay off - as the loader in loader.s leaves them.
EXIT            LD H,B
                LD L,C
                LD SP,(SAVED_SP)
                LD IY,$5C3A
                LD A,$3F
                LD I,A
                IM 1
                JP (HL)

SAVED_SP        DW 0
SAVE_BC         DW 0
SAVE_DE         DW 0
SAVE_HL         DW 0

                ASSERT $ <= SCHEDULE
                DS SCHEDULE - $

;; ===========================================================================
;; Which code each line runs: SCHEDULE[i] is the low byte of the line whose
;; head writes FIFO[i] (only 50 onwards are read; 312 is PRE, the next frame).
                ASSERT $ == SCHEDULE
                SCHEDULE_TABLE

;; ===========================================================================
;; The line routines. Every one of them is exactly 224 T-states, from its IN to
;; the next line's IN; the counts in the comments are T-states from the start
;; of the line.

LINES_PAGE      EQU HIGH $

;; Z: a paper line that decodes nothing.
Z_LINE          HEAD                    ; 36
                PAD 224 - 36 - 39
                TAIL

;; I: one byte off the queue, if there is one.
I_LINE          HEAD                    ; 36
                EXX                     ; 40
                LD A,E                  ; 44
                CP IXL                  ; 52
                JP Z,I_EMPTY            ; 62
                LD A,(DE)               ; 69
                INC E                   ; 73
STATE_JP        JP H_MARK               ; 83  -> the handler, back at I_DONE
I_EMPTY         PAD 83 + HANDLER_T - 62 - 10
                JP I_DONE               ; 83 + HANDLER_T

;; PRE: the line before the border. Rewinds the FIFO for the new frame, saves
;; the alternate set and loads the first colours.
PRE             HEAD_AT FIFO + 0        ; 36
                LD SP,FIFO              ; 46
                LD DE,FIFO + 50         ; 56
                EXX                     ; 60
                LD (SAVE_BC),BC         ; 80
                LD (SAVE_DE),DE         ; 100
                LD (SAVE_HL),HL         ; 116
                LD C,$FE                ; 123
                BORDER_INIT             ; 158
                EXX                     ; 162
                PAD 224 - 162 - 10
                JP BORDER_START         ; 224

                ASSERT HIGH Z_LINE == LINES_PAGE && HIGH I_LINE == LINES_PAGE
                ASSERT HIGH D_LINE == LINES_PAGE && HIGH PRE == LINES_PAGE

;; D: two samples off the FIFO. POP AF puts the older one in F, where its edge
;; bit (6) is the Z flag, and the newer one in A, where ADD A,A moves it to S.
;; Every path is padded to come out at .done at 160.
D_LINE          HEAD                    ; 36
                POP AF                  ; 46
                JP Z,.e1                ; 56
                ADD A,A                 ; 60
                JP M,.e01               ; 70
                ; no edge in either
                SLL L                   ; 78
                SLL L                   ; 86
                PAD 160 - 86 - 10
                JP .done
.e01            SLL L                   ; 78
                ADD HL,HL               ; 89  the bit that ended at the second
                LD L,$3F                ; 96
                JP C,.e01q              ; 106
                PAD 160 - 106 - 10
                JP .done
.e01q           LD (IX+0),H             ; 125  a byte for the queue
                INC IXL                 ; 133
                LD H,1                  ; 140
                PAD 160 - 140 - 10
                JP .done
.e1             ADD A,A                 ; 60
                JP M,.e11               ; 70
                ADD HL,HL               ; 81  the bit that ended at the first
                LD L,$7F                ; 88  and one sample of the next
                JP C,.e10q              ; 98
                PAD 160 - 98 - 10
                JP .done
.e10q           LD (IX+0),H             ; 117
                INC IXL                 ; 125
                LD H,1                  ; 132
                PAD 160 - 132 - 10
                JP .done
.e11            ADD HL,HL               ; 81  the bit that ended at the first
                JP C,.e11q1             ; 91
                LD L,$3F                ; 98
                ADD HL,HL               ; 109 and a one-sample pulse: a 0
                LD L,$3F                ; 116
                JP C,.e11q2             ; 126
                PAD 160 - 126 - 10
                JP .done
.e11q1          LD (IX+0),H             ; 110
                INC IXL                 ; 118
                LD H,1                  ; 125
                LD L,$3F                ; 132
                ADD HL,HL               ; 143 (H was 1: no carry)
                LD L,$3F                ; 150
                JP .done                ; 160
.e11q2          LD (IX+0),H             ; 145
                INC IXL                 ; 153
                LD H,1                  ; 160
.done           PAD 224 - 160 - 39
                TAIL

;; The border rows, from make_lineloader.py: each the head (to FIFO + 1 + row),
;; EXX, its OUTs, EXX.
BORDER_START    BORDER_ROWS

;; POST: the first paper line. Puts the border's background back and the
;; alternate set.
POST            HEAD_AT FIFO + 49       ; 36
                LD A,BORDER_BG          ; 43
                OUT ($FE),A             ; 54
                EXX                     ; 58
                LD BC,(SAVE_BC)         ; 78
                LD DE,(SAVE_DE)         ; 98
                LD HL,(SAVE_HL)         ; 114
                EXX                     ; 118
                PAD 224 - 118 - 39
                TAIL

;; After the handler: back to the main set and on to the next line.
I_DONE          EXX                     ; 87 + HANDLER_T
                PAD 224 - 39 - 87 - HANDLER_T
                TAIL

;; ===========================================================================
;; Start: called from the BASIC with interrupts off and the screen cleared.
;; Sets the registers up, then syncs to the frame interrupt with IM 2: I = $39
;; points at the 48K ROM's spare $FF bytes, so the vector is $FFFF, where a JR
;; reads its offset from $0000 ($F3, -13) and lands on SYNC_ISR at $FFF4.
LOADER_INIT     LD (SAVED_SP),SP
                EXX
                LD DE,QUEUE
                EXX
                LD IX,QUEUE
                LD IY,LINES_PAGE * 256
                LD A,$39
                LD I,A
                IM 2
                ; The first interrupt can land anywhere in the 32 T-states the
                ; INT line is held for; the second is taken from a HALT.
                LD HL,.again
                EI
                HALT
.again          LD HL,.synced
                EI
                HALT
                ; Here at SYNC_T after the interrupt (ack 19, JR 12, INC SP
                ; twice 12, JP (HL) 4) - give or take the 0-3 T-states the
                ; HALT's own NOPs leave, which differ from one run to the next.
                ; That would move the border pattern, so take them out with the
                ; ULA: a read of the screen in the first fetch group of the
                ; paper (T-state 14335) is held until 6 into it, wherever in
                ; its first 6 it arrives. Aim the read's T1 at 1 in (the
                ; instruction starts 10 earlier), so 0-3 late still arrives in
                ; time, with one to spare either side.
.synced         DELAY 14335 + 1 - 10 - SYNC_T
                LD A,($4000)            ; done at 14335 + 6 + 3, exactly
                DELAY 69888 + PRE_T - 14344 - 10 - 10 - 7
                LD HL,$00FF             ; 10  wait for a sync; no edge yet
                LD C,0                  ; 7
                JP PRE                  ; 10

                IF $ > $FFF4
                DISPLAY "The loader is ", /D, $ - $FFF4, " bytes too big: move it down with -DLOADER_QUEUE"
                ENDIF
                ASSERT $ <= $FFF4
                DS $FFF4 - $

;; The interrupt lands here (see LOADER_INIT): drop the return address.
SYNC_ISR        INC SP
                INC SP
                JP (HL)
                DS $FFFF - $
                DB $18                  ; JR, its offset at $0000
                ASSERT $ == $10000
