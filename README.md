# 澄溪流域水文日仓

澄溪流域资料中心的白班从测站交接表录入观测和更正。库管冻结正式截点，需要时向该截点回退。夜班仅凭发行件和自己的持久账接班。审计员用成果号核对签出时的站点原行、升数、来源及回执。

## 运行

使用 Python 3.11 标准库。请在项目根目录运行。平台物化工程时提供标准回归入口。

```text
python3 -B check_project.py
python3 -B demo.py
```

演示从 `examples/shift.json` 读入固定日期及观测，在临时目录走完整链路。白班先签出 18 L。夜班接收两笔更正后签出 24 L。库管回到备份，白班在新分支更正 A 为 17 L。落后副仓先收缺件，保持旧账。补齐后两份副仓均签出 28 L、缺测一条。旧 24 L 成果和成功回执可按原号复领。

## 数据与计量

观测由 `(day, station, key)` 定位。`day` 采用 `YYYY-MM-DD`。请求号、站名和观测键为非空字符串。每行输入含 `day`、`station`、`key`、`before`、`after`。首次录入的前值用 `warehouse.source.validation.ABSENT_OBSERVATION`，其交换值为 `"absent"`。`None` 表示明确缺测，整数 `0` 表示有效零读数。读数为整数升。账面按日取各键当前值，明细按站点、观测键排序，有值行累加，缺测单独计数。原行保有正式前值及当前升数，出处携带分支、批号、从 1 开始的批内行号和源原执。

单个工作目录对应一个中心的源流。控制库保存备份状态、恢复登记及活动工作库指针。每条已登记分支有独立工作库，等待登记的准备文件在完成后就位。发行登记和主仓固定成果各有独立档案。每个副仓自持物化事实、边界、收件待办和固定成果。初始化与样例录入分别调用。重开会保留已提交的数据。

## 调用约定

从项目根目录可导入以下模块。常规源仓操作通过 `open_active_workspace` 取得活动工作库。该上下文持有控制库的活动工作库租约，请在离开它之后调用其他接收工作目录的高层接口。副仓连接由调用方关闭。

| 业务 | 公开入口 | 结果 |
| --- | --- | --- |
| I1 正式入仓 | `warehouse.source.batches.submit_batch(source, request_id, changes)` | 正式源回执，含分支、批号、请求号及行数 |
| I2 冻结备份 | `warehouse.recovery.checkpoints.capture_checkpoint(root, checkpoint_id, branch, through, finalize=True)` | 固定截点及状态。`finalize=False` 将材料留在准备中，原号原参可继续完成登记 |
| I3 恢复主仓 | `warehouse.recovery.restore.restore_source(root, restore_id, checkpoint_id)` | 恢复登记、来源截点、新分支；`read_restore_result(root, restore_id)` 查询已登记结果 |
| I4 封存供数 | `warehouse.export.issues.issue_delivery(root, issue_id, day=day, branch=branch, after=start, through=end, restore_id=None)` | `pending` 附缺项或 `sealed` 附 `package` 字节；`read_issue_result(root, issue_id)` 可按号查结果 |
| I5 离线接收 | `warehouse.replica.intake.receive_delivery(replica, request_id, encoded)` | `pending` 附缺项和旧边界，或 `published` 附成功回执、新边界和固定成果号；`read_receipt(replica, request_id)` 查询成功结果 |
| I6 日账与固定成果 | `warehouse.report.lookup.lookup_daily(store, day, freeze=False)`、`lookup_fixed_report(store, report_id)` | 有序原行、逐行出处、分站小计、全日升数、缺测数、边界及有则返回的接收原执 |
| I7 按站封存 | `warehouse.export.issues.issue_scoped_delivery(root, issue_id, day=day, branch=branch, stations=stations, after=start, through=end, mode=mode)` | 结果字段同 I4。`mode` 为 `advance`（交付 `(after, through]`）或 `opening`（交付站集在 `through` 的开账依据） |
| I8 按站接收 | `warehouse.replica.intake.receive_scoped_delivery(replica, request_id, day=day, branch=branch, stations=stations, through=end, parts=(), on_stage=None)` | `pending` 附缺件键和旧边界，或 `published` 附成功回执、新边界和固定成果号；按站成果另含升序 `covered_stations` |

`root` 为中心工作目录。`store` 传工作目录读取白班账，传 `warehouse.replica.progress.open_replica(path)` 返回的连接读取夜班账。`lookup_daily` 可封存指定日，副仓当前发布已有该日固定成果时直接返回该成果。进度使用 `read_progress(replica)` 查询。源回执既可用 `find_source_receipt(source, request_id, branch)` 查，也可用 `read_batch_receipt(source, branch, number)` 查。源请求以分支和请求号为作用域。恢复、发行和接收请求分别在各自控制库、发行档案和副仓内独立登记。

供数范围为 `(after, through]`，批次顺序和闭合终点随包冻结。普通包至少含一批。恢复包的 `after` 等于正式备份边界，恢复后尚未入新批时可以在该点接班。包的 JSON 包含版本、声明、批次和可选起账截面。`warehouse.export.packages.decode_package` 校验正式范围、原执和包内摘要，`encode_package` 可重编码。材料在中心受信渠道交换，摘要用于内容和截点一致性核对。同一声明可以分次抵达，已收到的相容批次和截面可持久保存。声明改变、跨分支混件、前像冲突或过期交付引发 `Conflict`。已完成的原请求可复领原回执。未知固定成果或不可恢复的备份引发 `NotFound`，无效输入引发 `WarehouseError`。

副仓收到新分支普通件可以等待接班及前序到达。接班证据齐备后按冻结截面起账，再按新分支正式顺序应用更正。提交把完整事实、固定成果、当前指针、边界和成功回执放在同一事务中。`receive_delivery` 支持可选同步 `on_stage` 回调，分别在新发布写入后提交前、提交后返回前通知 `before_commit` 与 `after_commit`。回调异常直接传出，前者回滚发布并保留已登记材料，后者保留成功终局。回调可用独立读连接观察当前账。

## 按站发行与扩站

按站包的外层为 `version`（整数 2）、`declaration` 和 `pieces`，声明携带材料键到内容摘要的 `sha256` 映射，拆件只从 `pieces` 取子集。`advance` 的材料键是正式批号的十进制字符串，正文为 `{"rows": [...]}`，每项含批内行号 `row`、原输入行 `change` 和正式源原执 `source_receipt`，空数组证明该批无本日授权行。`opening` 的材料键是站名，正文为 `{"station": 站名, "facts": [...]}`，列出该站在截止批的全部现存观测事实（含原分支出处），空数组即该站当日确无观测的完整覆盖声明。按站副仓固定观测日与供数分支，空库从批 0 接收首个 `advance`，之后可原站集推进或在原截点、其后严格扩站一次；扩站联合提交旧站尾段与新增站开账，覆盖站集、边界、日账和回执同一事务落库。

## 正常业务回归

- `tests/test_source_daily.py` 核对 V1 与 V2，包括重复初始化、原执复领、缺测与零升、明细顺序、分站口径及晚行冲突整批回滚。
- `tests/test_replication.py` 核对 V3，包括离线封存终点、逐批前像推进、发行续办、冲突、缺批和原子可见性。
- `tests/test_restore.py` 核对 V4、V5 与 V6，包括正式备份资格、确切历史截点、恢复登记复查、同号同正文的不同出处、两种初态接班与缺件补交。
- `tests/test_history.py` 核对 V7 与 V8，包括迟到旧包、原号领旧行、提交前回滚续办，以及提交后回复丢失的原成果和原执复领。
