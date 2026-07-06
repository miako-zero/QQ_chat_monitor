# QQ Chat Monitor
 
一个基于 NapCat / OneBot 的 QQ 聊天媒体监控下载与本地归档工具。
 
本项目可以监听指定 QQ 私聊或群聊中的图片、视频、合并聊天记录等内容，并自动下载媒体文件、保存聊天记录，提供 PyQt6 图形界面用于配置、监控、预览和归档查看。
 
## 功能特点
 
- 支持监听指定 QQ 私聊和群聊
- 支持图片、视频自动下载
- 支持合并聊天记录解析
- 支持嵌套合并聊天记录解析
- 支持聊天记录本地归档
- 支持按日期、联系人或群聊分类保存媒体
- 支持图形化配置 NapCat / OneBot 网络参数
- 支持图形化实时预览下载内容
- 支持聊天记录归档查看和媒体预览
- 支持视频预览，可选择是否自动播放
- 支持极简监控模式，降低长期后台运行占用
- 支持托盘最小化和退出时清理本程序启动的 NapCat / node 进程
- 支持 MD5 去重，避免重复下载同一份媒体文件
 
## 项目结构
 
```text
QQ_Chat_Monitor/
├─ app/                         # PyQt6 图形界面和配置逻辑
├─ downloader/                  # 实时监控、下载、归档核心逻辑
├─ config/
│  └─ settings.example.json     # 示例配置文件
├─ main.py                      # 主入口
├─ realtime_downloader.py       # 兼容旧入口
├─ 启动图形界面.bat              # 图形化模式启动脚本
├─ 启动监控下载.bat              # 极简监控模式启动脚本
├─ 使用说明.docx                 # 图文使用说明
└─ .gitignore
```
 
运行后会自动生成或使用以下目录/文件：
 
```text
ALL_Fold/                       # 下载媒体和聊天记录归档
downloaded_md5.json             # MD5 去重缓存
config/settings.json            # 本地真实配置，不建议上传
```
 
## 运行模式
 
### 图形化模式
 
适合配置、查看状态、预览媒体和查看归档。
 
双击：
 
```text
启动图形界面.bat
```
 
图形界面关闭时默认会最小化到右下角托盘。
如需彻底退出，请右键托盘图标并选择退出。
 
### 极简监控模式
 
适合长期后台监控，占用更低。
 
双击：
 
```text
启动监控下载.bat
```
 
该模式不加载 PyQt6 图形界面，只启动监控下载核心。
 
## 快速开始
 
### 方式一：使用 Release 完整包
 
推荐普通用户使用 Release 页面提供的完整包。
 
完整包一般包含：
 
- 内置 Python
- NapCat
- 启动脚本
- 图形化界面
- 使用说明
 
下载后解压，双击启动脚本即可使用。
 
### 方式二：从源码运行
 
源码仓库不包含内置 Python 和 NapCat 运行时。
 
你需要自行准备：
 
- Python 3.10+
- PyQt6
- aiohttp
- websockets
- requests
- NapCat
 
安装依赖示例：
 
```bash
pip install PyQt6 aiohttp websockets requests
```
 
复制示例配置：
 
```text
config/settings.example.json -> config/settings.json
```
 
然后按需修改 `config/settings.json`。
 
启动图形界面：
 
```bash
python main.py --gui
```
 
启动极简监控：
 
```bash
python main.py --minimal
```
 
## NapCat / OneBot 配置说明
 
本工具默认使用：
 
```text
NapCat HTTP: http://localhost:3000
WebSocket:  ws://localhost:18082
```
 
如果使用图形化界面，可以在界面中生成默认网络配置，并写入 NapCat WebUI 配置。
 
首次使用通常需要：
 
1. 启动图形界面
2. 生成默认网络配置
3. 填写默认登录 QQ 号
4. 写入 NapCat 配置
5. 启动监控
6. 使用手机扫码登录 QQ
7. 登录成功后开始监听和下载
 
## 归档结构
 
下载内容默认保存在：
 
```text
ALL_Fold/
```
 
新版归档结构示例：
 
```text
ALL_Fold/
└─ 2026-07-06/
   ├─ chat.json
   ├─ private_123456789/
   │  ├─ image/
   │  └─ video/
   └─ group_987654321/
      ├─ image/
      └─ video/
```
 
说明：
 
- `chat.json` 保存当天聊天记录
- `private_账号` 保存对应私聊媒体
- `group_群号` 保存对应群聊媒体
- 图片和视频分目录保存
- GUI 归档界面会根据聊天记录和本地媒体文件尽量还原聊天内容
 
## 配置文件
 
真实配置文件：
 
```text
config/settings.json
```
 
示例配置文件：
 
```text
config/settings.example.json
```
 
请不要把真实 `settings.json` 上传到公开仓库，因为其中可能包含：
 
- QQ 号
- NapCat token
- WebSocket token
- 监控对象账号或群号
 
## 不建议上传的内容
 
以下内容不应该提交到 GitHub 源码仓库：
 
```text
ALL_Fold/
downloaded_md5.json
config/settings.json
python/
NapCat.Shell.Windows.Node/
.monitor.lock
__pycache__/
*.log
```
 
完整可运行包建议放在 GitHub Release 中，不建议直接提交到源码仓库。
 
## 常见问题
 
### 为什么源码版不能直接双击运行？
 
源码版不包含内置 Python 和 NapCat。
如果想开箱即用，请下载 Release 中的完整包。
 
### 为什么图形化模式比极简模式更占资源？
 
图形化模式会额外加载 PyQt6、媒体预览、归档扫描和日志界面。
长期后台监控建议使用极简模式。
 
### 为什么视频或大量归档预览会卡？
 
视频预览、缩略图生成、大量聊天记录扫描都可能占用 CPU、内存和磁盘 IO。
如果归档很多，建议按日期查看，不要一次性加载全部内容。
 
### 为什么没有下载？
 
请检查：
 
- NapCat 是否已登录
- OneBot HTTP 是否为 `http://localhost:3000`
- WebSocket 是否连接到本工具监听端口
- token 是否一致
- 监控对象 QQ 号或群号是否填写正确
- 是否开启了对应的私聊或群聊监听
 
## 隐私与安全提醒
 
本工具会保存聊天记录和媒体文件到本地。
请妥善保管 `ALL_Fold` 目录，不要上传到公开仓库或分享给无关人员。
 
如果你要公开发布项目，请确认没有上传：
 
- 聊天记录
- 图片、视频
- QQ 号
- 群号
- token
- NapCat 登录缓存
 
## 免责声明
 
本项目仅用于个人数据归档和学习研究。
请遵守 QQ、NapCat、OneBot 及相关平台的使用规则。
请勿用于侵犯他人隐私、批量抓取、传播未授权内容或其他违法违规用途。
 
## License
 
如果你暂时还没确定开源协议，可以先不填写 License。
如果准备开源，建议后续补充明确的许可证，例如 MIT License。
