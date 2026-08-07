# 自定义窗口外壳

## 1. 基本结构

顶层窗口使用：

```python
self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
```

中央 `windowFrame` 承担背景、边框和圆角；内部从上到下为：

1. `WindowTitleBar`；
2. 内容区；
3. 可选状态栏。

`windowFrame` 的 layout 正常状态保留 1 px margin，最大化时 margin 和 border 归零，避免屏幕边缘出现缝隙。

## 2. 标题栏按钮

使用 `QToolButton + QPainter` 绘制 pin、minimize、maximize、restore 和 close：

- 线宽约 1.55；
- 抗锯齿；
- 圆端点、圆连接；
- pin 选中时为强调蓝；
- close 悬停时图标变白、背景变 `#e81123`。

原因：系统字体中的符号在不同 Windows、缩放比例和字体环境下会变形或错位。

## 3. 拖动与双击

标题栏左键按下时优先调用：

```python
handle = window.windowHandle()
if handle is not None:
    handle.startSystemMove()
```

这比手工记录鼠标偏移更稳定，也保留 Windows 的贴靠行为。双击标题栏切换最大化/还原。

## 4. 最大化状态同步

在 `changeEvent(WindowStateChange)` 中：

- 切换 maximize/restore 图标；
- 更新 tooltip；
- 更新标题栏顶角圆角；
- 更新 `windowFrame` border、radius 和 margin。

不要只在按钮点击回调里更新，因为用户可能通过系统快捷键、任务栏或窗口贴靠改变状态。

## 5. 原生边缘缩放

Windows 无边框窗口需要在 `nativeEvent` 中处理 `WM_NCHITTEST (0x0084)`：

- 边界宽度推荐 7 px；
- 四边返回 HTLEFT/HTRIGHT/HTTOP/HTBOTTOM；
- 四角返回 HTTOPLEFT/HTTOPRIGHT/HTBOTTOMLEFT/HTBOTTOMRIGHT；
- 最大化时不处理。

这恢复系统原生 resize 光标和拖拽体验。非 Windows 平台直接回退 `super().nativeEvent()`。

## 6. 窗口置顶

更改 `WindowStaysOnTopHint` 会重建 native window，因此必须保存并恢复：

```python
visible = self.isVisible()
state = self.windowState()
self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
if visible:
    self.show()
    self.setWindowState(state)
```

正常可见窗口可再 `raise_()` 和 `activateWindow()`。不要在最小化状态下强行激活。

## 7. 多窗口同步置顶

主窗口和子串口窗口共享一个 window group：

- group 保存统一 `always_on_top` 状态；
- 新子窗口继承该状态；
- 任一标题栏 pin 改变时遍历成员同步；
- 应使用弱引用或关闭时移除成员，避免持有已销毁窗口。

## 8. 关闭路径

顶层窗口关闭前依次停止：

- 周期发送 timer；
- 自动流程；
- 诊断刷新；
- pending request；
- 串口线程；
- 子窗口。

不得依赖 Python 进程退出自动清理串口。
