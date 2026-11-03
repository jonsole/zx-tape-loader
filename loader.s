
                DEVICE ZXSPECTRUM48

                DEFINE tape	"loader.tap"
                EMPTYTAP tape

                INCLUDE 'basic.s'

                TAPOUT tape,0		    ;; Standard 17-byte file header
                DB	0		            ;; File type
                DB	'123       '        ;; File name
                DW	BASIC_LEN	        ;; File total length
                DW	0		            ;; Start basic line
                DW	BASIC_LEN	        ;; Pure basic length
                TAPEND

                TAPOUT	tape		    ;; File body
                ORG	23755

BASIC_START     
            LINE
                DB REM                 ; REM
CODE_START      DI

                ; Copy loader to uncondended RAM
                LD HL,LOADER_SOURCE
                LD DE,LOADER_DEST
                LD BC,LOADER_SIZE
                LDIR

                ; Clear screen
                LD HL,$4000
                LD DE,$4001
                LD (HL),L
                LD BC,6144
                LDIR      

                ; Set attributes to bright white
                LD (HL),64+7
                LD BC,768
                LDIR          
                
                LD IY,COUNTER_DRAW
                LD D,7
                JP LD_BYTES 

LOADER_SOURCE   DISP LOADER_DEST
                INCLUDE 'tape.s'
                INCLUDE 'counter.s'
                ENT                
LOADER_SIZE     EQU $-LOADER_SOURCE
                ;; Where the loader runs from: the top of RAM unless the build says otherwise
                ;; (sjasmplus -DLOADER_AT=$9000 - build_tape.py does this for a tape design).
                ;; Anywhere in uncontended memory will do; below $8000 the ULA's contention
                ;; would stretch the cycle-counted bit loop.
                IFDEF LOADER_AT
LOADER_DEST     EQU LOADER_AT
                ELSE
LOADER_DEST     EQU $FFFF - LOADER_SIZE
                ENDIF
                ASSERT LOADER_DEST >= $8000 && LOADER_DEST + LOADER_SIZE <= $10000
            LINE_END

            ;; The CLEAR puts RAMTOP, and the stack the loader runs on, just below this line's
            ;; number: by default just above BASIC, or wherever the build says (sjasmplus
            ;; -DSTACK_AT=$FE4D - build_tape.py does this to keep a block's way clear).
            LINE
                DB CLEAR,'0'
                DB $0E,$00,$00
                IFDEF STACK_AT
                DW STACK_AT
                ELSE
                DW BASIC_START + BASIC_LEN + 128
                ENDIF
                DB $00,':'
                DB RANDOMIZE,USR,'0'
                DB $0E,$00,$00
                DW CODE_START
                DB $00
            LINE_END

BASIC_LEN  = $-BASIC_START

                TAPEND


