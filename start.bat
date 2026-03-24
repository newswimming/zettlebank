@echo off
:: ZettleBank Server — launch wrapper
:: Sets SPACY_MODEL to the bundled model so the exe finds it without rebuild.
setlocal

set "HERE=%~dp0"
set "SPACY_MODEL=%HERE%_internal\en_core_web_sm"

echo Starting ZettleBank server...
echo SPACY_MODEL=%SPACY_MODEL%
"%HERE%zettlebank-server.exe"
