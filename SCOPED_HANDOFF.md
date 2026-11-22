# 按站接班与扩站

夜班北闸副仓已发布到批2。扩站时可接着用扩站前封存的北闸件 `(1,4]`。批3只有南闸行，北闸件须证明这批已覆盖。南闸按批4开账后断源合签，批4应有北闸 A 19 L 和南闸 B 17 L，合计36 L。北闸 A 来自批4原第2行。此前的北闸11 L成果仍可复领。`examples/scoped_shift.json` 提供五批原始入仓行。批5把南闸改为23 L，用来区分指定截点与最新账。

## 调用

`warehouse.export.issues.issue_scoped_delivery(root, issue_id, *, day, branch, stations, after, through, mode)` 沿用 `issue_delivery` 的结果字段。资料未正式齐备时返回 `pending`，封存后为 `sealed`，`package` 是 UTF-8 JSON 字节。同号同参复领原封存件，同号改参抛 `Conflict`。`day`、分支号、批号和站名沿用 README 的格式。`stations` 为非空的无重复站名列表或元组，按集合比较，输出为升序列表。这里的站集由可信值班员传入。

- `advance` 交付 `(after, through]`，要求 `after < through`。按指定分支的正式历史交付该日该站集的全部范围内原行。该分支已正式继承的历史仍保留原分支出处。每个正式批都有对应的覆盖材料，无选中行时也要有独立材料。
- `opening` 交付指定站集在 `through` 的完整开账依据，`after <= through`。`after` 是开账依据可用的截点下界。结果必须涵盖每个声明站点在 `through` 的全部现存观测，实际行的原出处可以早于 `after`。站点在该日确无观测时也要有完整覆盖声明。开账正文的表示由实现定义并说明，仍遵循下列传送约定。

`warehouse.replica.intake.receive_scoped_delivery(replica, request_id, *, day, branch, stations, through, parts=(), on_stage=None)` 接收多次可送达的包字节序列 `parts`。同一副仓文件用于一种接班方式，按站副仓与全量副仓分别建库，都用原 `open_replica` 入口。按站副仓固定观测日与供数分支，空库从批0接收首个 `advance`。之后允许原站集继续推进，也允许在原截点或其后严格扩站一次。跨分支、换日、缩站或再次扩站抛 `Conflict`。

原范围推进和扩站都以副仓当前截点为准。若旧站需要推进，使用声明站集为原站集的一个 `advance` 发行号，其范围须覆盖实际所需尾段。它可以包含已入账前缀，也可以封到更晚。所需尾段之外的批次可省送，收到也无需应用。扩站另收一个 `opening` 发行号，其站集须恰为新增站，且声明截止批须等于本次发布截止批。所需站点的依据齐备后，旧站只从实收起点向前核对前像，新站只依自己获证的开账材料建立事实。原行的缺测与零升沿用旧日账口径。

接收返回 `status` 为 `pending` 或 `published` 的字典。待办返回 `request_id`、`missing` 列表和旧 `boundary`，缺件可用原请求号继续补交，已接纳材料重开后仍可用。请求号绑定日、分支、目标站集、截止批，并保留所接纳的各发行声明。材料的范围、摘要、前像或同号声明冲突抛 `Conflict`，无效输入沿用 `WarehouseError`。成功结果沿用 `status`、`request_id`、`receipt_id`、`boundary` 和 `report_id`，同号同参可原样复领。`parts=()` 可查待办或继续结算已持有的材料。`read_receipt` 在成功前返回 `None`，成功后返回原回执。

覆盖、事实、批界、当前指针、固定成果和成功回执一起提交。`on_stage` 沿用 README 的提交前后回调语义，提交前回调抛错时回滚发布并保留已接纳材料。`lookup_daily(replica, day)` 与 `lookup_fixed_report(replica, report_id)` 沿用原计量字段，按站成果另有 `covered_stations` 升序列表。已覆盖却没有行的站仍列入 `covered_stations`。固定报告的 `boundary`、`intake_receipt` 和逐行 `provenance` 保留签出时的值。旧全量调用、样例及原回归保持有效。

## 传送

每份 JSON 包的外层为 `version`（整数2）、`declaration` 和 `pieces`。`declaration` 有 `issue_id`、`day`、`branch`、`stations`、`after`、`through`、`mode`、`sha256`。`sha256` 是材料键到内容摘要的映射，摘要沿用 `warehouse.export.packages.package_fingerprint` 对 JSON 值的算法。`pieces` 是材料键到内容的映射。拆件只从 `pieces` 取子集，声明照原样交付。副仓可以接到同一发行声明的重复件。摘要用于核对受信声明所绑定的材料，沿用现有中心受信渠道。

`advance` 的材料键为正式批号的十进制字符串。每份材料是 `{"rows": [...]}`，数组按原行号升序，每项有 `row`（从1开始的正式批内行号）、`change`（原输入行）、`source_receipt`（该行正式源原执）。`rows: []` 表示已证无本日授权行。只收到声明或摘要时，对应的正文仍是缺件。观察批3时可以保留其声明及摘要，从 `pieces` 取掉 `"3"`，稍后用同一声明仅补这份材料。`opening` 的材料键与正文由实现定义，封存和补交仍使用同一信封。

## 核验与演示

`python3 -B scoped_shift.py baseline` 使用原接口从样例跑全量发行和断源接收，核对第2、4、5批分别为16、36、42 L。完成新能力后，`python3 -B scoped_shift.py verify` 跑全套原回归和按站样例，失败时退出非零。`python3 -B scoped_shift.py demo` 显示北闸旧账、缺件待办和补齐后的双站日账。将最终 `.pointwise-runtime.json` 的 `verify` 和 `demo` 登记为对应命令的参数数组，沿用原清单的版本和执行类型。联合样例在发行后移走源仓，接收只用发行字节及副仓文件。
