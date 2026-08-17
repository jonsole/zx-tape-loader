
LD_BYTES	LD C,$55        ; Store the value in the C register (+80 for 'off' and +01 for 'on' - the present EAR state).

; The first stage of reading a tape involves showing that a pulsing signal actually exists (i.e. 'on/off' or 'off/on' edges).
LD_START	CALL LD_EDGE_1  ; Return with the carry flag reset if there is no 'edge' within approx. 14,000 T states. But if an 'edge' is found the border will go cyan.
            JR Z,LD_START

            LD  HL,0

;; Now accept only a 'leader signal'.
LD_LEADER	LD B,0              ; The timing constant.
            CALL LD_EDGE_1	    ; Continue only if two edges are found within the allowed time period.
            JR Z,LD_START
            LD A,32   	        ; However the edges must have been found within about 3,000 T states of each other.
            CP B
            JR NC,LD_SYNC
            INC HL	            ; Count the pair of edges in the H register until '256' pairs have been found.
            JR LD_LEADER
LD_SYNC     DEC H
            JP Z,LD_START
            CALL LD_EDGE_1	    ; The finishing edge of the 'on' pulse must exist. (Return carry flag reset.)
            JR Z,LD_START

            ; B - edge counter
            ; E - current byte (done when 1s shifted into C)
            ; C - current edge (01010101 rotated)
            ; D - border stripe pattern
            SCF

; The screen loading loop is used to load a block of screen bytes from header (also loaded)
            LD B,-2             ; 7
            JR .SCR_LOOP

.SCR_BLK    LD E,$100 >> 8      ; 7     Load 8 bits
            CALL C,LD_BITS      ; 17
            RET NC              ; 5     Exit if error
            LD (HL),E           ; 7     Store byte
            INC L               ; 6     Increment address
            DEC IXL             ; 8     Decrement length remaining
            JP P,.SCR_BLK       ;       Loop back if more to load in this block

.SCR_LOOP   ; Load 5 bits (block address MSB)
            LD E,$100 >> 5      ; 8     
            CALL C,LD_BITS      ; 17
            DEC E               ; 4
            JP M,.CTRL_LOOP     ; 10
            LD H,E              ; 4     Set MSB of address             

            ; Load 8 bits (block address LSB)
            LD E,$100 >> 8      ; 7    
            CALL C,LD_BITS      ; 17
            LD L,E              ; 4     
            SET 6,H             ; 8
            
            ; Load 5 bits (block size)
            LD E,$100 >> 5      ; 8     
            CALL C,LD_BITS      ; 17
            LD IXL,E            ; 8     Set byte counter
            JP .SCR_BLK

; The byte loading loop is used to fetch the bytes one at a time.

.CTRL_LOOP  LD E,1              ; 7     Load 8 bits (block address MSB)
            CALL C,LD_BITS      ; 17
            LD H,E              ; 4     Set MSB of load address
            LD E,1              ; 7     Load 8 bits (block address LSB)
            CALL C,LD_BITS      ; 17
            LD L,E              ; 4     Set LSB of load address
            LD E,1              ; 7     Load 8 bits (block length MSB)
            CALL C,LD_BITS      ; 17
            LD IXH,E            ; 4     Set MSB of byte counter
            LD E,1              ; 7     Load 8 bits (block length LSB)
            CALL C,LD_BITS      ; 17
            LD IXL,E            ; 4     Set LSB of byte counter
            DEC H
            JP P,.MEM_BLK
            JP (IX)
.MEM_BLK    LD E,1              ; 7  Load 8 bits
            CALL LD_BITS        ; 17
            RET NC              ; 5  Exit of error
            LD (HL),E           ; 7  Store byte
            INC HL              ; 6  Increment address
            DEC IX
            LD A,IXH
            OR IXL
            JP NZ,.MEM_BLK         ; 10 Loop back if more to load in this block
            SCF
            JP .CTRL_LOOP

LD_BITS:    ; Update border stripe
            RLC D               ; 8
            LD A,D              ; 4
            AND 1               ; 7
            OUT ($FE),A         ; 11 (37)    

            ; Delay for 38 + (4 + 17 + 4 + 8 + 24) = 95 tstates
            EXX                 ; 4
            CALL DELAY_CALL     ; 17
            EXX                 ; 4

            ; Measure 1st pulse
.PULSE_A    DEC B               ; 4
            RET Z               ; 5
            IN  A,($FE)         ; 11
            ADD A               ; 4
            XOR C               ; 4
            JP  P,.PULSE_A      ; 10 (38)

            ; Jump if a long pulse (1 bit)
            LD A,B              ; 4
            CP -8               ; 7
            JP C,.PULSE_1       ; 10 (21)

            ; Measure 2nd (short pulse )
.PULSE_B    DEC B               ; 4
            RET Z               ; 5
            IN A,($FE)          ; 11
            ADD A               ; 4
            XOR C               ; 4
            JP M,.PULSE_B       ; 10 (38)

            LD B,-1             ; 7         Reset counter 
            RL E                ; 4         Shift in 0
            JP NC,LD_BITS       ; 10        Loop if not final bit
            DEC B               ; 4         Adjust for extra t-states
            RET                 ; 10

            ; Pulse was long (1) 
.PULSE_1    RLC C               ; 8         Expect polarity change after long bit
            LD B,-2             ; 7         Reset counter (29 t-states long than 0 bit)
            SLL E               ; 8         Shift in 1
            JP NC,LD_BITS       ; 10        Loop if not final bit
            DEC B               ; 4         Adjust for extra t-states
            RET                 ; 10

LD_EDGE_1	LD A,22	        ; 7
LD_DELAY	DEC A           ; 4
            JR NZ,LD_DELAY  ; 12/7

LD_SAMPLE	INC B	        ; 4  Count each pass.
            RET Z	        ; 5  Return zero set if 'time-up'.
            IN A,($FE)      ; 11 Bit 6 is EAR
            ADD A           ; 4  Shift the byte, bit 7 is EAR.
            XOR C	        ; 4  Now test the byte against the 'last edge-type'; jump back unless it has changed.
            JP P,LD_SAMPLE  ; 10
            RLC C           ; 8
            LD A,C	        ; 4  Change the 'last edge-type' and border colour.
            AND $02	        ; 7  Keep only the border colour and last edge.
            OR  $80         ; 7  
            OUT ($FE),A     ; 11
            RET             ; 10


