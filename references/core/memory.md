# 精简本地档案

## 首次征求明确同意

在当前咨询已经能提供帮助后再介绍可选记忆。先说明保存哪些内容、保存路径、用途和管理方式，等待明确答复。例如可以这样问。

> 要不要在你指定的本机私人目录保存一份精简档案？内容包括关系别名、当前问题、你确认的关键事实、行动和实际进展，用于下次续聊。默认不保存完整聊天和图片。你随时可以查看、修改、暂停或删除。它是本地明文文件，设备使用者可能看到，也可能被你的同步或备份软件复制。你可以继续只在本次对话聊。

沉默、继续咨询、同意一次建议都不等于同意保存。用户同意后再确定专用目录，例如用户数据目录中的 emotion-master-private。保存范围改变或新参与者进入时重新确认适用授权。不要放在仓库、Skill目录或其子目录。通过脚本保存之前，确保精简输入文件也放在已同意的私人目录中，避免先在Git目录生成个人资料。

## 工具契约

使用Python 3.9+标准库CLI [memory.py](../../scripts/memory.py)。所有命令都需要 `--root PRIVATE_DIR`。下面的 PRIVATE_DIR、INPUT_JSON、DEST_JSON 是占位路径，调用时替换成真实绝对路径，按当前shell正确引用。

```text
python scripts/memory.py --root PRIVATE_DIR status
python scripts/memory.py --root PRIVATE_DIR grant --confirm-consent
python scripts/memory.py --root PRIVATE_DIR recall --case-id relation-01
python scripts/memory.py --root PRIVATE_DIR save --case-id relation-01 --input INPUT_JSON --expected-revision 0
python scripts/memory.py --root PRIVATE_DIR show
python scripts/memory.py --root PRIVATE_DIR pause
python scripts/memory.py --root PRIVATE_DIR resume --confirm-consent
python scripts/memory.py --root PRIVATE_DIR export --destination DEST_JSON
python scripts/memory.py --root PRIVATE_DIR delete-case --case-id relation-01 --confirm
python scripts/memory.py --root PRIVATE_DIR revoke --confirm
python scripts/memory.py --root PRIVATE_DIR purge --confirm
```

`--confirm-consent` 只表达已经取得的用户同意，不能由模型自行赋予。删除命令中的 `--confirm` 同样需要对应用户指令，不额外重复询问已经明确授权的删除。

- grant开启记忆。pause保留已有资料，禁止咨询召回和保存。用户仍可主动查看、导出、删除。
- resume需要重新确认恢复授权。revoke撤销记忆并删除脚本管理的全部案例，保留撤销状态。purge删除状态文件，可处理损坏状态。删除后只留下不含个人信息的锁文件。
- show用于用户主动查看全部资料，不能用于绕过暂停继续咨询。recall才是咨询召回入口。
- save写入完整当前摘要，必须先召回当前revision，新案例用0。相同内容重试不重复记事件。并发过期更新拒绝写入，读取新版再合并事实。
- 每个案例保留最近50次更新，最多30个案例。需要保留更早进展时先由用户选择导出。达到案例上限先由用户选择删除对象。
- 同一行动状态回退须由真实纠错支持，再用 `--correction`。更新事件保留旧摘要用于回查，敏感信息需要删除时删除整个案例并按用户要求重新建最小摘要，明确旧事件也会被删除。
- 正常保存使用文件锁和原子替换，损坏档案报错并停止，禁止自动重置。异常中断可能留下 `.emotion-master-writing-*.tmp`，purge同时清除专用目录内这些受管理临时文件。输入JSON与导出副本也要纳入用户删除范围，脚本不擅自删除任意外部文件。
- 这些命令管理本机文件，无法删除聊天平台历史、云同步或用户自行备份的副本。需要时明确列出仍由其他系统管理的副本。

## 精简案例结构

这里只用合成数据示范。字符串最多1200字符，未知项可空。保存前由用户纠正关键事实，别名和来源位置不含真实身份。required字段为relationship、goal、summary、source。

```json
{
  "relationship": "已婚，同住，单人咨询",
  "goal": "商量晚间照护分工",
  "summary": "用户报告这周三次独自哄睡，伴侣尚未参与本次咨询",
  "needs": "稳定休息和共同承担",
  "partner_position": "待本人确认",
  "constraints": "用户报告两人工作时间不同",
  "recommendation": "提出一项明确的完整照护责任",
  "tradeoffs": "需要协调工作与休息",
  "unknowns": "对方可承担的时段",
  "next_check": "谈过以后看双方明确回应",
  "source": "用户本轮陈述",
  "action": {
    "id": "care-01",
    "text": "提出周三晚间照护安排",
    "status": "selected",
    "source": "用户说准备去谈"
  }
}
```

action可省略。agreed必须增加partner_source，说明双方确认的出处。attempted与completed必须有feedback，记录真实执行与结果，source指向实际反馈。结构校验只能检查这些字段存在，代理仍须核对证据是否真的支持它们。没有真实结果时保留selected或agreed。复杂案例多项行动可分别放在不同议题案例中，保持每份摘要只跟踪当前关键行动。

## 宿主差异

路径访问失败时说清当前未保存，继续提供本轮咨询。跨会话只有在实际加载到已获授权的目录后才能召回。文件使用本机账户权限，未实现加密或双人分权验证，敏感或受监控设备场景优先使用无档案模式。用户明确要求删除后只报告实际完成的范围。
