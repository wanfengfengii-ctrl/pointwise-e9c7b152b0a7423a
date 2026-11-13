# 剖面存储的已公开接口与迁移合同

## 使用者与运行
采样处理程序把一条剖面视作按位置排列的可空有符号整数序列；批量重采、拼接和同一模板重复出现很常见。位置不是样本身份，插删后必须重排。命令仅用于回归，产品是供数据管道调用的库。
现有入口为 ledger.Store(path)，提供 apply(rid,ops)、read(name,start,stop,snapshot=None)、measure(name,start,stop,snapshot=None)、close()。同一实例串行调用，不同实例、不同进程允许并发。path是SQLite文件路径；不存在时创建format=1空库。所有位置从0起，区间左闭右开。
正常回归：python3 checks.py normal。编码器回归：python3 checks.py pages。演示：python3 checks.py demo。python3 samples.py PATH在不存在的PATH构造完整旧库；不是迁移器。运行清单中的verify最终应同时执行保留的旧断言和新增迁移业务核对；demo展示真实迁移前后、续跑及回收结果。

## 完整旧操作和返回值
ops是操作数组，依次执行，整个数组一个事务。每项为下列之一：
- ["put",name,source]：创建或替换剖面。
- ["splice",name,at,delete_count,source]：在已有剖面at处删除delete_count项，再插入source。at可等于长度；删除范围须在序列内。
- ["delete",name]：删除已有剖面，不影响快照或其他剖面已引用的内容。
- ["pin",sid]：把执行到此处全部剖面根固定为不可变命名快照，sid不得重复。
- ["drop",sid]：删除已有命名快照。

source是以下递归表达式之一：
- ["literal",[7,null,-2]]：0到64个值。值只能为null或[-2^63,2^63-1]内整数，布尔不是整数。
- ["range",name,start,stop]：复制执行到此处该剖面的区间。自引用splice读取插删前的根；不会读取插到一半的内容。
- ["repeat",source,times]：重复子序列times次；times为非负整数，允许空序列。

name、sid、rid为非空字符串。每个逻辑序列长度不超过2^63-1；计数和求和使用精确整数，不得溢出或转为浮点。literal中的null占据位置但不计入present及total。
apply返回{"generation":G,"profiles":{"名称":[length,present,total]}}；profiles反映批末当前剖面，不含已删剖面。每个新成功rid使generation加1，空ops也加1。请求身份为ledger.pack(ops)的完整规范内容；已成功rid同内容优先返回原回执，不检验当下引用是否仍存在，且不增加代次；异内容抛ValueError("REQUEST_CONFLICT")。回执不能跟随最新状态重新计算。
read返回原位置序列，例如[7,null,-2]，单次长度最多4096。measure返回[length,present,total]，可覆盖万亿级区间；不限制逻辑跨度。snapshot=None读取当前根，否则读取指定快照；每次读取固定同一完整事务快照。
非法source、长度、边界、未知剖面/快照或重复快照均整批回滚，包含新节点、根、快照、代次、日志及回执。错误名称及判断先后以ledger.py为准；保留原有调用参数、返回值、异常及请求重放语义。

## 旧库的完整物理格式
ledger.py中的DDL为权威格式，无外部文件和隐式服务。meta只有(single=1,format=1,generation)一行。nodes不可变，每个node记录kind、JSON body及长度/非空数/和；total为十进制文本。
四种kind及body：
- literal：[值……]；
- slice：[旧节点ID,start,stop]；
- concat：[子节点ID……]，按序拼接；
- repeat：[子节点ID,times]。
边只引用已存在节点，因此是有向无环图，可共享。heads把当前name映射到根。pins.roots是name到根ID的规范JSON。receipts保留规范请求和原回执。commits每个成功代次恰一行，其changes为["head",name,root或null]或["pin",sid,roots或null]的有序数组，同时携带rid、request、reply；空批也有日志。
旧代码不会回收节点或日志。既有SQL触发器在format不再为1时拒绝旧业务表写入及generation改动；迁移后不得移除或削弱此数据库栅栏。测试可在切换前打开原版Store并在切换后调用它，也可直接执行原版写SQL。
samples.py的INITIAL和EDIT就是完整兼容样本。第二批后A=[7,7,null,-2,7]，B前9项=[null,-2,7,7,null,-2,7,7,null]，B的measure为[3000000000002,2000000000002,5000000000014]；before快照的A仍为[7,null,-2,7]。B虽有三万亿位置，旧库仅11个节点。samples.history(store)另提供80次尾部插入的完整历史；python3 checks.py history核对81项序列及旧表达式高度160，展示为什么不能原样复制旧链作为新版索引。允许用这些样本构造真实文件，不要求二进制库预先存在。

## 新存储与升级交付
vendor/page2.py是完整可用的第二代存储依赖，不需下载。它包含p2_objects/p2_edges的真实DDL、二进制页编解码及Pages.leaf/branch/info；必须使用该格式承载全部新版采样值及结构，不得只改format或保留旧JSON执行器作为新读取路径。可以增加根、回执、检查点和回收等元数据表，不得改变page2已有列的含义。
leaf最多64项，固定9字节头部及每位置8字节载荷，null位载荷必须为零；branch按slot连接1或2条边，每条边将child重复copies次。length、present、total和height须符合该真实展开的精确聚合；branch载荷为空。节点不可原地改写，已有读者或快照继续引用旧根。

新增入口migration.Migration(path).step(limit)，limit为正整数，返回{"done":布尔,"generation":整数}。generation为迁移已完整追平的业务代次，初始为0；done=true表示已切换format=2。重复调用已完成迁移返回当前代次且不重迁。每步至多解码limit个尚无已提交转换结果的旧节点；已持久完成的节点映射不得因重开或竞争重建。首次可固定一次根集合和旧代次，随后仅沿新增commits及必要依赖推进，不得每步全扫旧nodes、heads或pins。迁移解码不得持有SQLite写事务。有限旧写后反复step须完成，不能要求人工停写、重建全部旧库或独占整个迁移过程。
复制期间旧apply持续可用；一个已固定的旧节点可被多根或多次重复引用，必须保留共享而非按出现次数复制。新旧位置映射须随slice、嵌套repeat及跨层splice精确保真。切换必须在同一原子提交中接住最后完整代次的当前根、全部仍存快照和历史回执并修改format。复制途中pin/drop/delete、同名剖面删除后复用、空批、旧请求重放以及任意持久化边界中断均不能丢失或混代。多个step实例竞争时，已公布映射和切换只能有一个一致结果。
升级后的ledger.Store自动按format读写，公开接口不变，format=2仍支持所有旧source和ops；新写立即被旧read/measure接口看到。原版旧进程的新请求必须被数据库拒绝，不能靠内存版本检查；原回执的只读重放可以继续成功。目标格式不得将业务写回旧表。旧nodes/heads/pins/commits可冻结作审计；切换后的所有读、新写、快照、回执和重开均不得查询旧业务表，迁移控制元数据除外。

## 新版操作的资源与可见性边界
不得展开repeat或逐位置复制range，也不得在每次splice中重写整个剖面。设L为所访问剖面的逻辑长度，每个非空根的height不得超过2*ceil(log2(L+1))；空剖面使用空leaf。measure的页访问次数为O(log(L+2))，read读取r项为O(log(L+2)+r)。一次新操作若source语法节点数为K，新增页数为O((K+1)*log(L+2))，L取操作涉及的新旧序列最大长度；不得随既有编辑次数、整个逻辑长度或重复次数线性增长。输入验证、根目录操作和返回profiles的目录枚举不计入页访问界；不能借此读取或展开载荷。迁移允许处理全部可达旧物理节点，不能把与历史编辑长度成正比的链直接照搬为新查询链。

新增Store.gc()返回{"objects":实际删除的p2_objects数量}；它不增加业务代次，不改回执。迁移完成后可反复调用，必须实际删除不再从当前剖面、保留命名快照或正在进行的读取可达的新版对象及其边；共享对象只能在最后一个必要引用解除后释放。当前根、快照根、回收与并发apply/read必须保持同一快照语义；GC崩溃、重试或与新写竞争不能误删新引用。可在GC中扫描新版对象元数据，不能以保留全部旧回执对应的新版根或永久保留全部新版节点代替回收。SQLite正在进行的旧读事务可以依靠其快照保护，无需另发明读者协议。
交付迁移、旧接口兼容实现和恢复说明，保留原业务断言；验证须实际核对旧文件升级、并发旧写与step、杀进程重开、不可展开的大repeat、连续插删后的索引界及共享快照解除后的物理回收。不得把正常进程退出当作任意持久化边界中断的全部证据。
