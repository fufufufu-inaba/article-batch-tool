# Batch Article Generation Prompt

请按 `article_prompt_template.md` 的要求生成若干篇英文供应商／制造商榜单文章。文章写作、名单核验、事实核验、标题规则、目标公司规则、HTML结构、CTA规则和发布前准确性要求，均以 `article_prompt_template.md` 为准。

本 prompt 只补充“批量处理、图片整理、Banner、质检文件和最终交付”的执行要求。不要重复或覆盖 `article_prompt_template.md` 中已有的写作规则。

榜单数量统一遵循主模板：优先10家，其次8家。目标公司固定计入1个名额、不在核实范围内；有至少9家通过核验的其他公司时输出10家，有7至8家通过核验的其他公司时输出8家；其他合格公司不足7家时，保留目标公司并按实际可靠数量输出，在内部质检中标记 NEEDS_REVIEW，不强行凑数。文章标题和Banner标题不显示具体数量。

目标公司必须出现在每篇榜单中、排在前四，并按主模板至少出现3次。目标公司不参与官网、身份、产品、地区、About Us及资料完整度核实，不得因这些条件删除、替换或暂停文章。使用用户提供的信息，不编造未提供的具体事实；其他公司仍须完整核验。该规则适用于两种文章发布方案。

## 输入信息

模板路径：

```text
{{ARTICLE_PROMPT_TEMPLATE_PATH}}
```

输出目录：

```text
{{OUTPUT_DIR}}
```

文章素材路径，按文章顺序填写：

```text
1. {{RUN_DIR_1}}
2. {{RUN_DIR_2}}
3. {{RUN_DIR_3}}
```

文章发布方案：

```text
{{ARTICLE_STYLE}}
```

`ARTICLE_STYLE` 使用 `company-profile` 或 `no-company-images`，必须与调研资料目录中的方案一致。`no-company-images` 不添加企业介绍图片。

Banner 素材路径：

```text
{{BANNER_SOURCE_DIR}}
```

目标公司：

```text
{{TARGET_COMPANY_NAME}}
{{TARGET_URL}}
```

目标公司主要产品和推广方向：

```text
{{TARGET_PRODUCTS_AND_PROMOTION_DIRECTION}}
```

目标客户：

```text
{{TARGET_CUSTOMERS}}
```

希望获得的匹配询盘：

```text
{{DESIRED_INQUIRIES}}
```

文章主题色：

```text
{{THEME_COLOR}}
```

## 执行流程

批量文章中的目标公司按文章输入顺序安排在第1、2、3、4名，第5篇起循环该顺序，避免每篇都排第一。用户明确指定某篇名次时优先使用指定值。名单排序确定后，同步更新Quick Navigation、公司标题编号、锚点、图片及映射文件；发布前质检检查目标公司名次轮换，最终汇报注明每篇目标公司名次。

1. 逐个读取文章素材目录。
2. 固定保留目标公司，仅对其他公司按 `article_prompt_template.md` 进行名单核验、产品证据核验、地区口径核验和事实核验。
3. `company-profile`方案整理必选目标公司及通过核验的其他最终入选公司图片；`no-company-images`方案不整理、不上传企业截图。不得因目标公司缺图而删除其正文或名单位置。
4. 为每篇文章生成一篇英文 HTML 文件。
5. 为每篇文章生成一张 Banner。
6. 汇总所有最终图片到 `images_for_upload`。
7. 生成映射和质检文件。
8. 最后汇报交付结果。

## 图片汇总要求

`company-profile`方案：从每个文章素材目录中筛选最终榜单公司的首屏截图。只保留最终入选公司的图片；如果某公司被名单核验淘汰，对应图片不要进入最终上传目录。

`no-company-images`方案：不为榜单公司整理或上传首屏截图，不在每家公司介绍下放企业图片。`images_for_upload` 和 `image_upload_manifest_template.csv` 只包含文章顶部主图／Banner；不得为公司图片创建映射行。

统一放入：

```text
{{OUTPUT_DIR}}\images_for_upload
```

要求：

- 英文命名
- 压缩到约 100KB
- 不严重模糊
- 不空白
- 不使用加载失败截图
- 不使用明显错误网站截图

图片前缀规则仅用于 `company-profile`方案中的企业截图；`no-company-images`方案无需企业图片前缀，Banner使用其独立文件名：

```text
第 1 篇：01, 02, 03...
第 2 篇：11, 12, 13...
第 3 篇：21, 22, 23...
第 4 篇：31, 32, 33...
第 5 篇：41, 42, 43...
```

生成：

```text
image_upload_manifest_template.csv
```

字段：

```text
article_index
article_file
image_type
rank
company_name
local_filename
public_url
```

`image_type` 使用：

```text
company
banner
```

`no-company-images`方案中只使用 `banner`；`company` 类型只用于 `company-profile`方案。

`public_url` 先留空，等待 FTP 上传后再回填。

## Banner 素材选择

如果提供了 N 张 Banner，按顺序一一对应文章。

如果提供的是 Banner 素材文件夹，则根据文章主题选择：

- 最相关
- 清晰
- 非空白
- 非失败截图
- 非明显重复
- 有工业场景或产品相关视觉

禁止使用：

- 空白图
- 全白失败图
- 网页未加载截图
- 严重模糊图
- 与专题明显无关的图片

生成：

```text
banner_source_mapping.csv
```

字段：

```text
article_index
article_file
banner_filename
source_image_path
source_status
notes
```

`source_status` 使用：

```text
selected
skipped_blank
skipped_unusable
needs_review
```

## Banner 制作规则

每篇文章生成 1 张 Banner。

尺寸：

```text
1600 x 900
```

样式：

- 背景图铺满
- 可适度压暗背景
- 中间增加黑色半透明横条
- 黑色半透明矩形必须从左边缘铺到右边缘
- 不得只包住文字
- 白色标题居中
- 标题上下保留合理空间
- 标题不得超出矩形
- 标题不得贴边

Banner 标题必须与文章实际标题口径一致，并且不显示榜单数量。

Banner 输出要求：

- 英文文件名
- 约 100KB
- 同时保存到输出目录根目录和 `images_for_upload`

生成后检查：

- 1600 x 900
- 文件可正常打开
- 约 100KB
- 无数字榜单数量
- 黑色半透明矩形左右铺满
- 标题居中
- 不溢出
- 不贴边
- 背景相关
- 不是失败图

错误 Banner 删除并重新生成，不要保留错误版本。

## 发布前质检

每篇 HTML 完成后必须重新执行一次发布前质检。其他公司检查名单真实性、采购判断和询盘匹配；目标公司不在核实范围内，只检查其是否保留、排在前四、链接与用户输入一致及HTML和映射格式。不得把目标公司的产品匹配或资料完整度重新作为淘汰条件。

生成：

```text
article_quality_check.md
```

每篇文章包含：

1. 一句话结论，只能使用：

```text
可以发布
修改后发布
关键事实待核实，暂缓发布
```

2. 值得保留的内容，最多 3 条。

3. 优先处理的问题，只写真正重要的问题。每项包括：

```text
位置
问题性质：已确认问题 / 待核实 / 优化建议
当前证据
为什么有影响
怎么改
完成标准
```

4. 必须补充的资料。如果没有，写：

```text
暂无必须补充的资料。
```

5. 一个最值得修改的示例。只改最重要的一处；英文原文使用英文修改。不确定内容使用：

```text
[待确认：XXX]
```

## 最终交付内容

输出目录内应包含：

```text
images_for_upload\
image_upload_manifest_template.csv
company_url_mapping.csv
banner_source_mapping.csv
article_quality_check.md
N 篇 HTML 文件
N 张 Banner
```

Banner 需要同时存在于：

```text
{{OUTPUT_DIR}}
{{OUTPUT_DIR}}\images_for_upload
```

## 最终汇报

完成后简要告诉我：

- 输出目录
- 实际生成了几篇 HTML
- 每篇文章最终总公司数，并分别注明必选目标公司1家及通过核验的其他公司数量；不得将免核实目标公司表述为“已经独立核实”
- 每篇文章最终标题
- `images_for_upload` 中共有多少张图片
- `company_url_mapping.csv` 是否生成
- `image_upload_manifest_template.csv` 是否生成
- `banner_source_mapping.csv` 是否生成
- `article_quality_check.md` 是否生成
- 是否存在 NEEDS_REVIEW
- 是否存在“关键事实待核实，暂缓发布”
- 是否有候选公司因为身份不符而被删除
- 是否有候选公司因为目标产品证据不足而被删除
- 是否存在地区口径调整
- 是否存在技术配置需要产品／工程确认

最终汇报应明确每篇实际入选的公司数量，以及是否满足“优先10家、其次8家”的要求。不足8家时，说明其他合格公司不足的原因和 NEEDS_REVIEW 状态。目标公司必须保留，不计为核实失败或被删除的候选。

“标题和Banner不显示数量”只约束标题文案，不取消正文名单优先10家、其次8家的规则。
