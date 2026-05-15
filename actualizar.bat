@echo off
cd /d c:\REPORTESDEVOLUCIONES

echo [%date% %time%] Iniciando actualizacion... >> actualizar.log

python generador.py >> actualizar.log 2>&1
if errorlevel 1 (
    echo [%date% %time%] ERROR al generar el reporte. >> actualizar.log
    exit /b 1
)

"C:\Program Files\Git\bin\git.exe" add index.html dashboard.html pendientes.html

"C:\Program Files\Git\bin\git.exe" diff --cached --quiet
if errorlevel 1 (
    "C:\Program Files\Git\bin\git.exe" commit -m "Actualizar reporte [%date% %time%]" >> actualizar.log 2>&1
    "C:\Program Files\Git\bin\git.exe" push >> actualizar.log 2>&1
    echo [%date% %time%] Push realizado. >> actualizar.log
) else (
    echo [%date% %time%] Sin cambios, no se hizo push. >> actualizar.log
)
