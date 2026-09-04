# Sublime Text Rider Dark

这是为本机 Sublime Text 4 准备的 JetBrains Rider Dark 配色包。它只包含静态资源，不含插件和可执行代码。

色板以当前 VS Code 中启用的 `JetBrains Rider Dark Theme` 为基准，并按 Sublime Text 的 scope 体系重新映射：

- 编辑器背景：`#262626`
- 主界面背景：`#2B2D30`
- 标签栏深色表面：`#1E1F22`
- 编辑器正文：`#BDBDBD`
- 选区：`#09335E`

## 文件

- `Rider Dark.sublime-color-scheme`：编辑区及语法高亮。
- `Rider Dark.sublime-theme`：标题栏、侧栏、标签、状态栏和弹出面板。

## 手动安装

以下操作由你手动执行。本目录中的文件不会自动修改 Sublime Text。

1. 在 Sublime Text 中点击 `Preferences -> Browse Packages...`。
2. 打开其中的 `User` 文件夹。
3. 将本目录中的以下两个文件复制到 `User` 文件夹：

   - `Rider Dark.sublime-color-scheme`
   - `Rider Dark.sublime-theme`

   在当前 Windows 环境中，目标目录通常是：

   ```text
   C:\Users\shawn.zhang\AppData\Roaming\Sublime Text\Packages\User
   ```

4. 在 Sublime Text 中点击 `Preferences -> Settings`。
5. 只编辑右侧的用户配置。你当前配置可调整为：

   ```json
   {
       "theme": "Rider Dark.sublime-theme",
       "color_scheme": "Rider Dark.sublime-color-scheme",
       "themed_title_bar": true,
       "ignored_packages":
       [
           "Vintage"
       ]
   }
   ```

   注意 JSON 属性之间必须有逗号，不要修改左侧的默认配置。

6. 保存设置。Sublime Text 通常会立即重新加载主题，无需退出正在运行的程序。

也可以先复制文件，再通过菜单分别选择：

- `Preferences -> Select Theme -> Rider Dark`
- `Preferences -> Select Color Scheme -> Rider Dark`

## 排错

如果主题没有出现在选择菜单中：

1. 确认两个文件直接位于 `Packages\User`，没有多套一层目录。
2. 确认文件扩展名没有被 Windows 隐藏并变成 `.txt`。
3. 打开 `View -> Show Console`，检查是否有 `Error loading resource` 或 JSON 解析错误。

如果只有代码区变色而侧栏没变化，请确认配置中的 `theme` 是：

```json
"theme": "Rider Dark.sublime-theme"
```

如果标题栏仍由 Windows 控制，请确认：

```json
"themed_title_bar": true
```

## 恢复原状

把用户设置改回：

```json
"theme": "Default Dark.sublime-theme"
```

然后删除 `color_scheme` 这一行，或通过 `Preferences -> Select Color Scheme` 选择原来的配色。确认界面恢复后，可以再手动删除复制进 `Packages\User` 的两个 Rider Dark 文件。

## 说明

Sublime Text、VS Code 和 Zed 对语法 token 的划分并不完全相同，所以基础色值和主要语义可以对齐，但少数语言的局部高亮可能不会逐 token 完全一致。
