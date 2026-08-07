# ARQ 系统诊断工具 v1.1

## 源码启动

```powershell
python -m pip install -r requirements.txt
python main.py
```

工具使用 SPM-MCP 二进制协议。右侧 Alice/Bob 页签结构一致，各节点每秒采集一次，
两侧采集起点错开半个周期。周期采集只覆盖能直接定位 ARQ、资源、反馈、配置与错误的
关键寄存器；写脉冲、保留项和重复调试镜像不会被周期读取。

> skill 资产副本已移除来源电脑的 Conda 环境与工作区绝对路径。
