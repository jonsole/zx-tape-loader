
CODE	        EQU $AF
USR	            EQU $C0
REM             EQU $EA
LOAD            EQU $EF
RANDOMIZE       EQU $F9
CLEAR	        EQU $FD

LINE_NUM	=	0

LINE            MACRO 
	            DB HIGH LINE_NUM
	            DB LOW LINE_NUM
                LUA ALLPASS
                    sj.parse_code('dw line_' .. tostring(sj.calc("LINE_NUM")) .. '_length')
                    sj.parse_line(   'line_' .. tostring(sj.calc("LINE_NUM")) .. '_begin')
                ENDLUA
                ENDM

LINE_END        MACRO
	            db	#0D
                LUA ALLPASS
                    sj.parse_line('line_'
                        .. tostring(sj.calc("LINE_NUM"))
                        .. '_length = $ - line_'
                        .. tostring(sj.calc("LINE_NUM"))
                        .. '_begin')
                ENDLUA
LINE_NUM = LINE_NUM + 1
                ENDM

