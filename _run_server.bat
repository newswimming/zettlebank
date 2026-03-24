@echo off
cd /d "%~dp0"
"C:\Users\andrea\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe" -m uvicorn server:app --host localhost --port 8000
