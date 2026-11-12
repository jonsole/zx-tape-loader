;; The line-locked loader's tape: the same BASIC bootstrap as loader.s (code in a
;; REM, then CLEAR and RANDOMIZE USR), carrying linetape.s instead of tape.s.
;; Builds lineloader.tap; lineloader_gen.s comes from make_lineloader.py.

                DEVICE ZXSPECTRUM48

                DEFINE tape	"lineloader.tap"
                EMPTYTAP tape

                INCLUDE 'basic.s'
                INCLUDE 'lineloader_gen.s'

;; Memory, all at the top of RAM: by default above $F017, where Lunar Jetman's
;; image ends, which leaves about 2.4K for the border rows. A busier pattern or a
;; smaller game can move it down (sjasmplus -DLOADER_QUEUE=$E000), as far as
;; $8000: below that the ULA's contention would stretch the lines. The queue
;; and the FIFO are not in the image; SCHEDULE must sit $200 above the FIFO
;; (see TAIL in linetape.s). The image always ends at $FFFF.
                IFDEF LOADER_QUEUE
QUEUE           EQU LOADER_QUEUE
                ELSE
QUEUE           EQU $F100           ; 256-byte ring of decoded bytes
                ENDIF
                ASSERT QUEUE >= $8000 && LOW QUEUE == 0
FIFO            EQU QUEUE + $100    ; one edge byte a line, 312 a frame
SCHEDULE        EQU FIFO + $200
IMAGE_START     EQU FIFO + 312      ; the loader proper, up to $FFFF

;; The first byte after the sync.
LINE_MARKER     EQU $A5

;; Timing against the beam, checked on the zx-spectrum-emulator (see
;; tests/README.md). LINE_START is the T-state of each chunk's first
;; instruction (its IN) counted from the start of its line (224 * line), which
;; puts the IN in the horizontal blank; make_lineloader.py reads it to time the
;; border's OUTs. SYNC_ADJUST is how much later than 47 T-states after the frame
;; interrupt LOADER_INIT's .synced runs: it only has to be right to within a
;; T-state or so, since the contended read there takes out the rest.
LINE_START      EQU 144
SYNC_ADJUST     EQU 0

                TAPOUT tape,0		    ;; Standard 17-byte file header
                DB	0		            ;; File type
                DB	'linetape  '        ;; File name
                DW	BASIC_LEN	        ;; File total length
                DW	0		            ;; Start basic line
                DW	BASIC_LEN	        ;; Pure basic length
                TAPEND

                TAPOUT	tape		    ;; File body
                ORG	23755

BASIC_START
            LINE
                DB REM
CODE_START      DI
                LD HL,LOADER_SOURCE
                LD DE,IMAGE_START
                LD BC,LOADER_SIZE
                LDIR

                ; Clear the screen to white ink on black
                LD HL,$4000
                LD DE,$4001
                LD (HL),L
                LD BC,6144
                LDIR
                LD (HL),64+7
                LD BC,767
                LDIR

                LD A,BORDER_BG
                OUT ($FE),A
                JP LOADER_INIT

LOADER_SOURCE   DISP IMAGE_START
                INCLUDE 'linetape.s'
                ENT
LOADER_SIZE     EQU $-LOADER_SOURCE
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
