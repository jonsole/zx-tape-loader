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
;; the alternate set, loads the border's colours from COLOURS (B' D' E' H' L'
;; and, in A', the background for POST) and jumps to the border rows: the black
;; loop until the tape's border block has replaced BORDER_JP's low byte.
PRE             HEAD_AT FIFO + 0        ; 36
                LD SP,FIFO              ; 46
                LD DE,FIFO + 50         ; 56
                EXX                     ; 60
                LD (SAVE_BC),BC         ; 80
                LD (SAVE_DE),DE         ; 100
                LD (SAVE_HL),HL         ; 116
                LD C,$FE                ; 123
                LD HL,(COLOURS)         ; 139  L', H'
                LD DE,(COLOURS + 2)     ; 159  E', D'
                LD A,(COLOURS + 4)      ; 172
                LD B,A                  ; 176  B'
                LD A,(COLOURS + 5)      ; 189
                EX AF,AF'               ; 193  the background, for POST
                EXX                     ; 197
                PAD 224 - 197 - 10
BORDER_JP       JP BLANK_ROW0           ; 224

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

;; POST: the first paper line. Puts the border's background back and starts
;; the attribute band. The loader's own alternate set stays saved until
;; BAND_EXIT: nothing between here and there wants it.
POST            HEAD_AT FIFO + 49       ; 36
                EX AF,AF'               ; 40  the background, from PRE
                OUT ($FE),A             ; 51
                LD (BAND_SAVE_HL),HL    ; 67  the byte being decoded
                EXX                     ; 71
                LD A,(BAND_PHASE)       ; 84
                ADD A,LOW BAND_VAL + 1  ; 91  line 1 (line 0 was written last frame)
                LD C,A                  ; 95
                LD B,HIGH BAND_VAL      ; 102 B'C' -> line 1's colour
                LD DE,BAND_ROWLO + 1    ; 112 D'E' -> line 1's row end
                LD H,HIGH BAND_ROW0_END ; 119
                LD A,(DE)               ; 126
                LD L,A                  ; 130 H'L' = line 1's row end
                LD A,(BC)               ; 137
                EXX                     ; 141
                LD H,A                  ; 145
                LD L,A                  ; 149 HL = line 1's colour, twice
                LD (BAND_V0),HL         ; 165 which is next frame's line 0 too
                LD B,BAND_LINES - 1     ; 172
                PAD 189 - 172
                JP BAND_TAIL            ; 199

;; ===========================================================================
;; The attribute band: rows 0-7 of the paper (scanlines 64-127), columns
;; BAND_X to BAND_X + 13, rewritten every scanline with that scanline's colour
;; from BAND_VAL - which moves up a scanline a frame (BAND_PHASE).
;;
;; A scanline's attributes must be written after the ULA has read them for the
;; scanline before and before it reads them for this one, and never while the
;; ULA is fetching, or the write would be held and the line stretched. Both are
;; met at the turn of the line: the ULA fetches 79 to 206 T-states into a band
;; line, and reads column x at about 82 + 4x. So one PUSH (two columns) goes at
;; 213 of the line before, the IN at 0 as ever, then six PUSHes, the last
;; written at 74, and the rest of the head after them. Seven PUSHes: 14 columns.
;;
;; While the band runs: HL is the colour (BAND_SAVE_HL keeps the decoder's),
;; B counts the lines, H'L' is the row end SP starts each line from, D'E'
;; walks BAND_ROWLO and B'C' walks BAND_VAL. SP is free: no D line has run yet
;; this frame, so it is put back as FIFO.
BAND_TAIL       EXX                     ; 203
                LD SP,HL                ; 209 this line's row end
                EXX                     ; 213
                PUSH HL                 ; 224 its last two columns (written 218, 221)
BAND_LOOP       IN A,($FE)              ; 11
                DUP 6
                PUSH HL                 ; 77  the other twelve, the last at 74
                EDUP
                XOR C                   ; 81  the rest of the head
                LD (DE),A               ; 88
                INC DE                  ; 94
                XOR C                   ; 98
                LD C,A                  ; 102
                DEC B                   ; 106
                JP Z,BAND_EXIT          ; 116
                EXX                     ; 120
                INC E                   ; 124
                LD A,(DE)               ; 131
                LD L,A                  ; 135 next line's row end
                INC C                   ; 139
                LD A,(BC)               ; 146
                EXX                     ; 150
                LD H,A                  ; 154
                LD L,A                  ; 158 next line's colour
                PAD 189 - 158
                JP BAND_TAIL            ; 199

;; The last band line: put the loader's alternate set back, and start on line
;; 0 for next frame, which no scanline shows until then.
BAND_EXIT       EXX                     ; 120
                LD BC,(SAVE_BC)         ; 140
                LD DE,(SAVE_DE)         ; 160
                LD HL,(SAVE_HL)         ; 176
                EXX                     ; 180
                LD HL,(BAND_V0)         ; 196
                PAD 203 - 196
                LD SP,BAND_ROW0_END     ; 213
                PUSH HL                 ; 224
BAND_END        IN A,($FE)              ; 11
                DUP 6
                PUSH HL                 ; 77
                EDUP
                XOR C                   ; 81
                LD (DE),A               ; 88
                INC DE                  ; 94
                XOR C                   ; 98
                LD C,A                  ; 102
                LD A,(BAND_PHASE)       ; 115 a scanline further for next frame
                INC A                   ; 119
                LD B,A                  ; 123
                CP BAND_WRAP            ; 130
                SBC A,A                 ; 134 (FF while below BAND_WRAP)
                AND B                   ; 138
                LD (BAND_PHASE),A       ; 151
                LD HL,(BAND_SAVE_HL)    ; 167
                LD SP,FIFO              ; 177
                PAD 224 - 177 - 39
                TAIL

BAND_SAVE_HL    DW 0
BAND_V0         DW 0
BAND_PHASE      DB 0

;; After the handler: back to the main set and on to the next line.
I_DONE          EXX                     ; 87 + HANDLER_T
                PAD 224 - 39 - 87 - HANDLER_T
                TAIL

;; ===========================================================================
;; The border until the tape brings its pattern: 48 rows of black, each one the
;; head (its FIFO slot through H') and nothing else. B' counts the rows.
;; BLANK_ROW0 and BORDER_ROWS must share a page, so that BORDER_JP switches
;; from one to the other with its low byte alone, in one write between frames.
                IF HIGH $ != HIGH ($ + 64)
                DS 256 - LOW $
                ENDIF
BLANK_ROW0      HEAD_AT FIFO + 1        ; 36
                EXX                     ; 40
                LD HL,FIFO + 2          ; 50
                LD B,47                 ; 57
                EXX                     ; 61
                PAD 224 - 61 - 10
                JP BLANK_ROW            ; 224
BLANK_ROW       IN A,($FE)              ; 11
                XOR C                   ; 15
                EXX                     ; 19
                LD (HL),A               ; 26
                INC HL                  ; 32
                EXX                     ; 36
                XOR C                   ; 40
                LD C,A                  ; 44
                PAD 224 - 44 - 32
                EXX                     ; 196
                DEC B                   ; 200
                EXX                     ; 204
                JP Z,BLANK_END          ; 214
                JP BLANK_ROW            ; 224
BLANK_END       JP POST                 ; (the last row's 10 T-states, as the JP above)

;; The border's colours as PRE loads them: L', H', E', D', B', then the
;; background. All 0 (black) until the tape's border block brings its own.
COLOURS         DB 0, 0, 0, 0, 0, 0

;; Where the border block's rows go, straight after COLOURS. Until then this
;; holds LOADER_INIT, which has run by the time anything is loaded.
BORDER_ROWS_AT
                ASSERT HIGH BLANK_ROW0 == HIGH BORDER_ROWS_AT

;; ===========================================================================
;; Start: called from the BASIC with interrupts off and the screen cleared.
;; Sets the registers up, then syncs to the frame interrupt with IM 2: I = $39
;; points at the 48K ROM's spare $FF bytes, so the vector is $FFFF, where a JR
;; reads its offset from $0000 ($F3, -13) and lands on SYNC_ISR at $FFF4.
LOADER_INIT     LD (SAVED_SP),SP
                ; Each band line's row end, and a black band until the tape
                ; brings its colours.
                LD HL,BAND_ROWLO
                LD DE,$0800 + LOW BAND_ROW0_END
.row            LD B,8
.line           LD (HL),E
                INC HL
                DJNZ .line
                LD A,E
                ADD A,32
                LD E,A
                DEC D
                JR NZ,.row
                LD HL,BAND_VAL
                LD B,128
.black          LD (HL),0
                INC HL
                DJNZ .black
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

IMAGE_END
