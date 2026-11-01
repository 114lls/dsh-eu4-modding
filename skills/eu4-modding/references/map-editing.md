# 地图编辑

> **这是 EU4 里最危险的操作**：改地图会让**所有存档失效**，且错误不会报错，
> 只会表现为「地图显示不对/省份不可点」。动手前一定备份。

## 一、地图相关文件

```
map/
├── default.map              地图总定义（海/陆、尺寸、气候归属）
├── provinces.bmp            省份 ID 位图（每像素 RGB 编码省份号）
├── terrain/                 地形贴图
├── heightmap.bmp            高度图
├── rivers.bmp               河流
├── area.txt                 区域（area）定义
├── region.txt               大区（region）定义
├── continent.txt            大洲归属
├── superregion.txt          超大区
├── climate.txt              气候
├── definitions.txt          ★ 省份 ↔ 坐标的映射（最关键）
└── province_names/          各省份名（按文化分文件）
```

## 二、改「已有省份」的属性（低风险）

只改 `history/provinces/*.txt`，**不碰 `map/`**：

```
# history/provinces/1-Uppland.txt
owner = SWE
controller = SWE
culture = swedish
religion = catholic
base_tax = 5
trade_goods = grain
```

这类改动不动位图，**不会破坏存档**。

## 三、新增省份（高风险，需同时改 4 处）

1. `map/provinces.bmp` —— 新像素用新的 RGB 值（不能与已有省份撞色）
2. `map/definitions.txt` —— 加 `ID;R;G;B;type;coords...`
3. `map/area.txt` / `region.txt` —— 新省份必须归属到某个 area 和 region
4. `history/provinces/<ID>-<name>.txt` —— 省份初始数据

**漏任何一步的症状**：

| 漏项 | error.log | 症状 |
|---|---|---|
| `area.txt` | `PROVN hasn't been assigned to an area!` | 省份不可选中/无归属 |
| `continent.txt` | `N (PROVN) is not assigned to a continent` | 大洲相关逻辑失效 |
| `provinces.bmp` | `Province N has no pixels in provinces.bmp` | 省份在地图上不存在 |
| 本地化省名 | `Missing province adjective! ID: N, expected loc key: PROV_ADJN` | 显示原始 key |

实测这些报错都真实存在（见 `references/errorlog.md` 的分类表）。

## 四、改区域 / 大区划分（中风险）

`map/area.txt` 与 `map/region.txt` 是扁平列表：

```
my_area = {
    1 2 3 4
}
```

改这个会改变「区域」归属（影响任务、贸易、建筑等），但**通常不破坏存档**。

坑：同一个省份被重复加入两个 area：

```
[colonialregion.cpp:63]: Adding province double N to colonial region xxx
```

## 五、绝对不能做的事

| 禁止 | 原因 |
|---|---|
| 改 `provinces.bmp` 的尺寸 | 整个地图坐标系统失效 |
| 删已有省份 | 引用该省的历史/事件全部失效 |
| 改已有省份的 RGB 值 | 等价于删掉旧省 + 新建一个 |
| 用未压缩的 BMP 格式差异 | 必须是 PDX 要求的 BMP 位深 |

## 六、工具

本技能包**不做地图编辑自动化**（改位图需要图像库，且风险太高不适合脚本化）。

建议的外部工具（只读体会，不建议抄代码）：

- `lnxnb/eu-toolkit` —— 交互式地图编辑 + 字节级最小 diff
- `happy-dwarf-team/happydwarf-website` —— EU4 开源工具集散地

## 七、验证流程

1. **备份**：`python "$S\backup.py" "D:\mods\MyMod" --tag before-map-edit`
2. 改一处（如只改 `history/provinces/`）
3. 进游戏，**新开档**（不要用旧存档）
4. 看 error.log：`python "$S\errorlog_triage.py" --only-mine`
5. 确认地图正常 → 再做下一处

**每次只改一类，改完立刻验证** —— 地图问题最难定位，因为它通常表现为
「某些完全不相干的功能坏了」。
