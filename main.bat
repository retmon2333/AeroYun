@echo off
chcp 65001 >nul
title 启动 AeroYun 播放器

cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [提示] 未检测到虚拟环境 venv，正在为您自动创建...
    python -m venv venv
    
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败！请确保已安装 Python 并已添加至系统环境变量。
        pause
        exit /b
    )
    
    echo [提示] 虚拟环境创建成功，正在升级 pip...
    .\venv\Scripts\python.exe -m pip install --upgrade pip
    
    REM 检查 requirements.txt 是否存在
    if not exist "requirements.txt" (
        echo [错误] 找不到 requirements.txt 文件！请确保该文件存在于当前目录下。
        pause
        exit /b
    )
    
    echo [提示] 正在从 requirements.txt 安装依赖包，请耐心等待...
    REM 如果下载慢，可以在下方命令末尾添加：-i https://pypi.tuna.tsinghua.edu.cn/simple
    .\venv\Scripts\python.exe -m pip install -r requirements.txt
    
    if errorlevel 1 (
        echo [错误] 依赖安装失败！请检查网络连接，或尝试更换 pip 镜像源。
        pause
        exit /b
    )
    echo [提示] 所有依赖安装完成！
) else (
    echo [提示] 检测到已存在虚拟环境。
)

echo [提示] 正在启动主程序 main.py ...
.\venv\Scripts\python.exe main.py

pause