NAME = BANKTEST
ARCHIVED = NO
LTO = NO
HAS_PRINTF = NO
PYTHON ?= python
EXTRA_ASM_SOURCES = $(OBJDIR)/translated.s

include $(shell cedev-config --makefile)

$(OBJDIR)/translated.s: translate.py control-flow.asm
	$(PYTHON) translate.py control-flow.asm $@
