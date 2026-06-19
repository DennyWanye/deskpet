# DeskPet PPT 模板目录

这是 DeskPet 的 PPT 模板目录。请在这里放正规的 `.pptx` 模板：模板必须包含 slide master、layout 和占位符；不要放由图片或自由文本框堆出来的 deck。

文件名去掉 `.pptx` 扩展名后就是模板名。agent 或用户可以用 `ppt_create(template="名字")` 选用对应模板。

标准布局会被映射填充：

- `title` / `section`
- `bullet` -> Title and Content
- `two_column` -> Two Content
- `image` -> Picture with Caption

模板放好后无需改代码，会自动被 `_list_bundled_templates()` 发现。
