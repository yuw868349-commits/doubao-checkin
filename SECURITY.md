# 安全说明

## 这个仓库不包含凭据

源码、示例、文档里没有任何真实 Cookie。`data/` 在 `.gitignore` 里，CI 也只从 Secrets 读。

## Cookie 等同于账号

`sessionid` 拿到就能操作你的豆包账号。请不要把 `data/cookies.json`、`data/shots/`、`data/checkin.log` 贴到 issue、PR 或聊天窗口里。

如果不小心泄漏：

1. 在豆包客户端和网页退出登录，让旧会话失效；
2. 重新登录，生成新的 Cookie；
3. 如果已经推到 GitHub，删掉对应提交并强制推送，同时检查 Actions 日志。

## 权限

脚本写出的 `data/cookies.json` 会尽量设成 `600`。在 Windows 上这个位不一定生效，建议把整个项目放在只有自己能访问的目录。

## 漏洞上报

发现安全问题请不要开公开 issue，发邮件到 yuw868349@gmail.com，标题以 `[doubao-checkin]` 开头。
