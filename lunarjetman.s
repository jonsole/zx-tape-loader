            DEVICE ZXSPECTRUM48

            ORG $7000

            LD HL,JETMAN_START+$7FFF
            LD DE,$8000+$7FFF
            LD BC,$8000
            LDDR

            LD A,0
            LD ($8009),A    ; Skip FRAMES check
            LD A,$E9
            LD ($5CB0),A    ; Write JP (HL) to $5CB0

            JP $8000

JET_START   INCBIN "lunarjetman.bin"
JET_END
            SAVEBIN "jetman.bin",$7000,BIN_END-$7000
