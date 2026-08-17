
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
LOADER_DEST     EQU $FFFF - LOADER_SIZE
            LINE_END

            LINE
                DB CLEAR,'0'
                DB $0E,$00,$00
                DW BASIC_START + BASIC_LEN + 128
                DB $00,':'
                DB RANDOMIZE,USR,'0'
                DB $0E,$00,$00
                DW CODE_START
                DB $00
            LINE_END

BASIC_LEN  = $-BASIC_START

                TAPEND


