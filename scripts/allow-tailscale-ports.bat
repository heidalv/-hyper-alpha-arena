@echo off
chcp 65001 >nul
echo ========================================
echo  AlphaArena Tailscale 远程访问 - 防火墙放行
echo  需要管理员权限（会弹 UAC）
echo ========================================
netsh advfirewall firewall delete rule name="AlphaArena-Frontend-5273" >nul 2>&1
netsh advfirewall firewall delete rule name="AlphaArena-Backend-8000-Any" >nul 2>&1
netsh advfirewall firewall add rule name="AlphaArena-Frontend-5273" dir=in action=allow protocol=TCP localport=5273 profile=any enable=yes
netsh advfirewall firewall add rule name="AlphaArena-Backend-8000-Any" dir=in action=allow protocol=TCP localport=8000 profile=any enable=yes
netsh advfirewall firewall add rule name="AlphaArena-Node-Private" dir=in action=allow program="C:\Program Files\nodejs\node.exe" profile=private,domain,public enable=yes
echo.
echo 已添加规则。远程请打开:
echo   http://100.100.175.17:5273
echo   后端: http://100.100.175.17:8000
echo.
pause
