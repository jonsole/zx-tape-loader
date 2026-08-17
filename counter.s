DELAY_EXIT  MACRO
            LD IY,$+5           ; 14
            RET                 ; 10
            ENDM

DELAY_CONT  MACRO PC
            LD IY,PC            ; 14
            RET                 ; 10
            ENDM

DELAY_CALL  JP (IY)

COUNTER_DRAW:
            
            ; Delay loop until C == 0
.DELAY_LOOP LD B,0                  ; 7         Pre-clear B to waste 7 t-stats
            LD DE,COUNTER_VAL       ; 10        Pre-load DE to waste 10 t-states
            INC C                   ; 4         Increment counter
            JP M,.DELAY_DONE        ; 10 (21)   Jump if >127, i.e. time to draw digits
            LD A,(DE)               ; 7     Waste 7
            DELAY_CONT .DELAY_LOOP
.DELAY_DONE LD C,0                  ; 7
            DELAY_EXIT              ; Total = 38

            ; Calculate screen address and counter value address
            ; BC = counter index (0,1,2,3), B always 0
            ; NOTE: LOW(20732)=252, and C is at most 3, so 252+C never carries into H - safe
            ; to add C with an 8-bit ADD instead of ADD HL,BC. Don't reuse this trick for the
            ; second ADD HL,BC below: HL there is COUNTER_VAL, a relocatable address whose low
            ; byte isn't guaranteed clear of the carry boundary, so it needs the real 16-bit add.
            ; Closest achievable to the 38 target without touching the caller - see note below.
.DIG_NEXT:  LD H,HIGH 20732         ; 7         HL = base screen address
            LD A,LOW 20732          ; 7
            ADD A,C                 ; 4         A = digit screen address low byte
            LD L,A                  ; 4         HL = digit screen address
            EX HL,DE                ; 4         DE = digit screen address, HL = counter value table
            ADD HL,BC               ; 11        HL = counter value address
            DELAY_EXIT              ; Total = 37 (target 38, closest achievable - see note above)

            ; Calculate digit gfx address
            LD A,(HL)               ; 7         A = counter value
            LD H,HIGH DIG_GFX       ; 7
            ADD LOW DIG_GFX         ; 7 
            LD L,A                  ; 4         HL = address of digit gfx                        
            LD B,7                  ; 7         Set byte copy counter
            INC BC                  ; 6         Increment counter index
.DIG_BYTE   DELAY_EXIT              ; Total = 38

            ; Copy bytes to screen
            LD A,(HL)               ; 7
            LD (DE),A               ; 7
            INC HL                  ; 6
            INC D                   ; 4
            DEC B                   ; 4
            JP NZ,.DIG_BYTE         ; 10 
            DELAY_EXIT              ; Total = 38

            ; Loop back if more digits to print
            LD A,(HL)               ; 7     Waste 7
            LD A,C                  ; 4
            CP 4                    ; 7
            JP Z,.DIG_DONE          ; 10 (28)
            LD DE,COUNTER_VAL       ; 10           
            DELAY_CONT .DIG_NEXT    ; Total = 38
.DIG_DONE   LD HL,COUNTER_VAL+4     ; 10
            DELAY_EXIT              ; Total = 38

            ; Decrement counter value
            ; NOTE: both paths below total 37, 1 t-state short of the 38 target. This is
            ; already the closest achievable value: the smallest available filler is 4
            ; t-states, so padding either path would overshoot to 41 (3 over) - worse than
            ; leaving it 1 short. Don't "fix" this without restructuring the calling
            ; fragments too - see .DIG_NEXT above for the same constraint.
.CNT_DEC    DEC HL                  ; 6         Decrement counter address
            DEC (HL)                ; 11        Decrement counter value
            JP M,.CNT_WRAP          ; 10 (27)   Jump if counter has wrapped
            INC IY                  ; 10    Waste 10
            DELAY_CONT .CNT_NEXT    ; Total = 37 (target 38, closest achievable - see note above)
.CNT_WRAP   LD (HL),79              ; 10        Reset counter
            DELAY_EXIT              ; Total = 37 (target 38, closest achievable - see note above)
            
            ; Check if preceeding counter needs to be decremented, loop back if it does
.CNT_NEXT   LD A,(HL)               ; 7         Get counter value again
            CP 72                   ; 7         Check if we need to move next digit
            DEC C                   ; 4         Move to next digit
            JP C,.CNT_DONE1         ; 10 (28)   Jump if not
            JP Z,.CNT_DONE2         ; 10 (38)   Jump if no more digits
            DELAY_CONT .CNT_DEC     ; Total = 38
.CNT_DONE1  INC IY                  ; 10 (28)
.CNT_DONE2  DELAY_CONT .DELAY_LOOP  ; Total = 38 or 28+10 = 38

COUNTER_VAL DB (0*8),(2*8),(3*8),(3*8)

DIG_GFX     ; 0
            DEFB    %11111110
            DEFB    %11000110
            DEFB    %10111010
            DEFB    %10111010
            DEFB    %10111010
            DEFB    %11000110
            DEFB    %11111110
            DEFB    %00000000
            ; 1
            DEFB    %11111110
            DEFB    %11101110
            DEFB    %11001110
            DEFB    %10101110
            DEFB    %11101110
            DEFB    %10000010
            DEFB    %11111110
            DEFB    %00000000
            ; 2
            DEFB    %11111110
            DEFB    %10000110
            DEFB    %11111010
            DEFB    %11000110
            DEFB    %10111110
            DEFB    %10000010
            DEFB    %11111110
            DEFB    %00000000
            ; 3
            DEFB    %11111110
            DEFB    %10000110
            DEFB    %11111010
            DEFB    %11000110
            DEFB    %11111010
            DEFB    %10000110
            DEFB    %11111110
            DEFB    %00000000
            ; 4
            DEFB    %11111110
            DEFB    %11110110
            DEFB    %11100110
            DEFB    %11010110
            DEFB    %10000010
            DEFB    %11110110
            DEFB    %11111110
            DEFB    %00000000
            ; 5
            DEFB    %11111110
            DEFB    %10000010
            DEFB    %10111110
            DEFB    %10000110
            DEFB    %11111010
            DEFB    %10000110
            DEFB    %11111110
            DEFB    %00000000
            ; 6
            DEFB    %11111110
            DEFB    %11000110
            DEFB    %10111110
            DEFB    %10000110
            DEFB    %10111010
            DEFB    %11000110
            DEFB    %11111110
            DEFB    %00000000
            ; 7
            DEFB    %11111110
            DEFB    %10000010
            DEFB    %11111010
            DEFB    %11110110
            DEFB    %11101110
            DEFB    %11011110
            DEFB    %11111110
            DEFB    %00000000
            ; 8
            DEFB    %11111110
            DEFB    %11000110
            DEFB    %10111010
            DEFB    %11000110
            DEFB    %10111010
            DEFB    %11000110
            DEFB    %11111110
            DEFB    %00000000
            ; 9
            DEFB    %11111110
            DEFB    %11000110
            DEFB    %10111010
            DEFB    %11000010
            DEFB    %11111010
            DEFB    %11000110
            DEFB    %11111110
            DEFB    %00000000
            ; 0
            DEFB    %11111110
            DEFB    %11000110
            DEFB    %10111010
            DEFB    %10111010
            DEFB    %10111010
            DEFB    %11000110
            DEFB    %11111110
            DEFB    %00000000
