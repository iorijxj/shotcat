#!/usr/bin/env python3
"""从剧本抽取设定：读项目全剧本 → GLM 抽取 角色/场景/道具 → 建实体(项目级)。
供"剧本页"一键使用；抽完可再跑 视觉词典(锁定细化) 与 AI拆镜头。
用法：python extract_setup.py <project_id> [--model glm-4.6]
"""
from __future__ import annotations
import argparse, json, re, time, urllib.error, urllib.request
from glm import chat_json
from http_util import get_all

SYS = """你是剧本设定抽取专家，同时担任本项目的艺术指导 agent。通读完整剧本后，先在内部统一判断故事类型、时代背景、摄影风格、人物关系和主要场景体系，再抽取设定；不要把这个内部判断作为单独字段输出。

艺术指导职责：
- 统筹所有抽取结果，让角色、场景、道具和服装符合同一个剧本、同一种年代质感、同一套摄影/美术风格。
- 角色描述必须符合角色年龄、身份、气质和剧情关系，不要写成随机美女/帅哥模板。
- 场景描述必须符合剧情发生地点的空间逻辑、时代痕迹和材质体系，不要把人物状态或剧情事件写进场景。
- 道具和服装必须服务角色身份、年代背景和剧情用途，不要添加脱离剧本的装饰性物品。

【强关联道具规则】
- 只有道具本身展示、记录或承载画面内容时才填写 visual_content，例如照片、毕业照、手机屏幕、监视器画面、画框、证件照、海报或带人物/地点图像的书页。
- visual_content.characters 和 visual_content.scenes 只能填写该道具内部画面中实际可见的角色与地点；普通手机、普通书本、纸条、钥匙等没有图像内容的道具必须留空，不能因为使用者或出现地点而强行关联。
- description 要说明道具内部画面应呈现什么，不重复道具本体材质；后续生成会将列出的角色与场景设定图作为强参考，必须与剧情一致。
【角色造型拆解硬规则】
- 一个角色不是一张“默认造型”。先识别剧本中所有可见且会影响生图一致性的状态组合，再分别输出到 looks。
- 每个状态必须由剧本证据支持，至少包含年龄/时期、身份、服装、发型或妆造、身体状态中的必要项；例如“少年学生时期·校服”“成年职场时期·通勤造型”“雨后淋湿状态”。
- 只在外观确实不同或剧情需要锁定时拆分。短暂情绪、单次动作、手持道具、镜头景别不能单独形成造型状态。
- 同一角色在不同年龄、时代、身份、特殊服装、明显妆发或受伤/污损状态下，必须拆成不同状态；状态名称短、具体且彼此不重复。
- base_appearance 只写稳定外貌；looks.description 只写该状态新增或需要锁定的可见信息。服装属于角色状态，不单独输出“默认服装”。
- 场景和道具遵循同一规则：空间/物件本体写在基础描述，时间、天气、使用痕迹、损坏或陈设变化等写在 states。只有视觉上确实不同的状态才拆分。
- 每个角色、场景、道具状态都必须指定且仅指定一个 is_base=true 的基准状态。其他状态必须以这张基准图为参考生成，保持同一张脸、同一空间结构或同一物件本体。

【是否拆分派生状态】
- 先判断“是否值得单独生成参考图”，而不是见到任何变化就拆资产。基础资产可被多个镜头复用；镜头自身的提示词负责修正本镜头的时段、光线、天气、情绪和动作。
- 角色：只有年龄/时代变化、身份转换、持续或剧情关键的服装妆发变化、明显伤病/污损、伪装等会改变识别或连续性的情况，才拆为派生状态。一次性的表情、姿势、轻微凌乱、普通日夜光线不拆。
- 场景：只有空间结构、年代、季节、核心陈设、长期损坏/改造、灾后状态等发生明显变化，或该状态会在多个镜头复用时，才拆为派生状态。仅一次出现的日夜、晨昏、轻微阴晴/雨雪、普通灯光开关，不拆新场景图，保留同一基础场景并由镜头提示词描述。
- 道具：只有本体外观、尺寸、内容物、可见损坏或关键文字发生持续且重要的变化时才拆。一次性的摆放方向、拿取动作、轻微反光不拆。
- 拆分不确定时默认复用基准资产；宁少拆、不要为一次性小差异生成孤立资产。每个输出 states 条目都必须是“需要单独参考图”的状态，不要列出由镜头提示词即可修正的微小变化。

抽取：
- 角色：所有有台词或明确动作的人物。
- 场景：独立地点（地点变化或同地点明显时间跳跃各算一个）。
- 物件（道具）判据——三选一才算，其余不列：①能被角色拿起/携带/递出/操作的可移动物品；②被台词点名或成为镜头/情节焦点的物品；③承载象征意义、推动情节的物品。
  【明确排除】车辆/房屋/门/窗/百叶窗/桌椅/沙发/地毯/方向盘/仪表/计价器/家具/建筑构件等固定或场景固有物，一律归入场景描述，绝不单列为道具。宁缺毋滥，只保留真正影响剧情的关键道具（通常一集 2-4 个）。
名称一律用剧本原文；描述写视觉化简述（后续会锁定细化，不必很长）。
【场景描述硬规则】
- 场景就是地点环境，不是剧情摘要。
- 只写地点类型、空间结构、方位布局、建筑/地面/墙面/门窗/树木/陈设/材质、光线、天气、年代痕迹。
- 不写任何人物、角色身份、动作、对白、剧情事件、回忆、幻影、情绪意义或叙事功能。
- 如果剧本只提供人物动作，请只保留动作发生的地点名称，并把描述写成空场景环境。
只输出 JSON。"""

USER_TMPL = """【完整剧本】
{script}

输出 JSON：
{{
  "characters": [{{"name":"", "base_appearance":"角色不随状态改变的外貌基础（性别、五官、体态、稳定发型特征等；不要写服装或手持物）", "looks":[{{"label":"造型状态名称（如少年学生时期·校服 / 成年职场时期·通勤造型）", "description":"这一状态下可见的年龄、身份、发型、妆容、服装、身体状态和年代细节"}}]}}],
  "scenes": [{{"name":"", "base_description":"空场景的稳定空间结构、建筑/地面/墙面/陈设/材质与年代痕迹；不得含人物、动作、剧情", "states":[{{"label":"场景状态名称（如清晨晴天 / 深夜雨后）", "description":"本状态可见的光线、天气、陈设或损耗变化；仍不得含人物、动作、剧情", "is_base":true}}]}}],
  "props": [{{"name":"", "base_description":"道具本体的稳定形态、尺寸、材质与年代特征", "states":[{{"label":"物品状态名称（如日常完好 / 使用磨损）", "description":"本状态可见的污损、破损、内容物或表面变化", "is_base":true}}], "visual_content":{{"description":"仅当道具本身展示/记录/承载角色或场景内容时，写出其中可见内容；普通道具留空", "characters":["只填写该道具中实际可见的角色名"], "scenes":["只填写该道具中实际可见的场景名"]}}}}]
}}"""

BASE = "http://localhost:8000/api/v1"


def clean_scene_text(value: str) -> str:
    # Do not try to maintain an endless blocklist here. Scene descriptions
    # are generated upstream as environment-only text; if empty, callers fall
    # back to the scene name.
    return (value or "").strip()


def _req(m, p, b=None, t=40):
    data = json.dumps(b).encode() if b is not None else None
    r = urllib.request.Request(BASE + p, data=data, headers={"Content-Type": "application/json"} if data else {}, method=m)
    try:
        with urllib.request.urlopen(r, timeout=t) as x:
            return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def items(p):
    return get_all(BASE, p)


def create_idem(path, body):
    c, r = _req("POST", path, body)
    if c < 400:
        return "created"
    msg = str(r.get("message", ""))
    if "already exists" in msg or "已存在" in msg:
        return "exists"
    print(f"    ! {path} {c} {msg[:60]}")
    return "fail"


def wait_get(path, tries=30):
    for _ in range(tries):
        if _req("GET", path)[0] == 200:
            return
        time.sleep(0.5)
    raise SystemExit(f"依赖未就绪 {path}")


def run(pid: str, model: str):
    proj = _req("GET", f"/studio/projects/{pid}")[1].get("data") or {}
    if not proj:
        raise SystemExit(f"项目 {pid} 不存在")
    style = proj.get("style") or "真人都市"
    visual = proj.get("visual_style") or "现实"
    chapters = sorted(items(f"/studio/chapters?project_id={pid}&page_size=100"), key=lambda c: c.get("index", 0))
    script = "\n\n".join(c.get("raw_text", "") for c in chapters)
    if not script.strip():
        raise SystemExit("项目无剧本正文，请先在剧本页粘贴剧本")

    print(f"[抽取设定] 项目 {pid}｜剧本 {len(script)} 字｜模型 {model}")
    data = chat_json(SYS, USER_TMPL.format(script=script), model=model, temperature=0.5, timeout=420)

    def pfx(raw):
        return f"{pid}__{raw}"

    def asset(etype, eid, name, desc):
        return create_idem(f"/studio/entities/{etype}", {
            "id": eid, "name": name, "description": desc,
            "style": style, "visual_style": visual, "project_id": pid,
        })

    # 重跑稳定性：先拉现有实体建 名称→id 映射，原名命中就复用旧 id；
    # 新 id 从现有最大序号之后接着分配，避免 GLM 枚举顺序变动导致 id 漂移/撞号。
    def alloc(etype, kw):
        existing = items(f"/studio/entities/{etype}?project_id={pid}&page_size=100")
        name2id = {e["name"]: e["id"] for e in existing}
        pat = re.compile(rf"^{re.escape(pid)}__{re.escape(kw)}_(\d+)$")
        mx = max((int(m.group(1)) for e in existing for m in [pat.match(e.get("id", ""))] if m), default=0)
        return name2id, mx

    def next_id(state, kw):
        state[1] += 1
        return pfx(f"{kw}_{state[1]:03d}")

    sc = data.get("scenes", []); pr = data.get("props", []); ch = data.get("characters", [])
    sc_n2i, sc_mx = alloc("scene", "scene"); sc_state = [sc_n2i, sc_mx]
    pr_n2i, pr_mx = alloc("prop", "prop"); pr_state = [pr_n2i, pr_mx]
    ch_n2i, ch_mx = alloc("character", "char"); ch_state = [ch_n2i, ch_mx]

    def store_state_family(entity_type, id_key, state, entries, *, base_field, legacy_field, title, fallback_label, extra_description=None):
        """将同一实体的基准与状态平铺成可独立引用的资产，并记录派生关系。"""
        count = 0
        for entry in entries:
            base_name = (entry.get("name") or "").strip()
            if not base_name:
                continue
            variants = [item for item in (entry.get("states") or entry.get("looks") or []) if isinstance(item, dict) and (item.get("label") or "").strip()]
            if not variants:
                variants = [{"label": fallback_label, "description": entry.get(legacy_field) or entry.get(base_field) or "", "is_base": True}]
            base_variant = next((item for item in variants if item.get("is_base") is True), variants[0])
            base_label = str(base_variant.get("label") or fallback_label).strip()
            base_asset_name = f"{base_name} · {base_label}"
            for index, variant in enumerate(variants):
                label = str(variant.get("label") or fallback_label).strip()
                asset_name = f"{base_name} · {label}"
                base = (entry.get(base_field) or entry.get(legacy_field) or "").strip()
                detail = (variant.get("description") or "").strip()
                relation = "【状态关系】基准" if label == base_label else f"【状态关系】派生自：{base_asset_name}"
                description = "\n".join(part for part in [
                    f"【{title}基础】{base}" if base else "",
                    f"【{title}状态】{detail}" if detail else "",
                    relation,
                    extra_description(entry) if extra_description else "",
                ] if part)
                legacy_id = state[0].get(base_name)
                has_state = any(existing_name.startswith(f"{base_name} · ") for existing_name in state[0])
                if index == 0 and legacy_id and not has_state and asset_name not in state[0]:
                    code, _ = _req("PATCH", f"/studio/entities/{entity_type}/{legacy_id}", {"name": asset_name, "description": description})
                    if code < 400:
                        state[0].pop(base_name, None)
                        state[0][asset_name] = legacy_id
                        count += 1
                        continue
                entity_id = state[0].get(asset_name) or next_id(state, id_key)
                asset(entity_type, entity_id, asset_name, description)
                count += 1
        return count

    def base_asset_name(entry, fallback_label):
        variants = [item for item in (entry.get("states") or entry.get("looks") or []) if isinstance(item, dict) and (item.get("label") or "").strip()]
        base_variant = next((item for item in variants if item.get("is_base") is True), variants[0] if variants else {})
        label = str(base_variant.get("label") or fallback_label).strip()
        return f"{entry.get('name', '').strip()} · {label}" if entry.get("name") else ""

    character_base_names = {entry.get("name"): base_asset_name(entry, "剧本当前造型") for entry in ch}
    scene_base_names = {entry.get("name"): base_asset_name(entry, "基础状态") for entry in sc}

    def prop_visual_content(entry):
        visual_content = entry.get("visual_content") or {}
        if not isinstance(visual_content, dict):
            return ""
        content = str(visual_content.get("description") or "").strip()
        character_names = [character_base_names.get(name, "") for name in visual_content.get("characters", []) or []]
        scene_names = [scene_base_names.get(name, "") for name in visual_content.get("scenes", []) or []]
        character_names = list(dict.fromkeys(name for name in character_names if name))
        scene_names = list(dict.fromkeys(name for name in scene_names if name))
        if not content and not character_names and not scene_names:
            return ""
        return "【强关联参考】内容：%s；角色：%s；场景：%s" % (
            content or "无",
            "、".join(character_names) or "无",
            "、".join(scene_names) or "无",
        )

    scene_count = store_state_family("scene", "scene", sc_state, sc, base_field="base_description", legacy_field="description", title="场景", fallback_label="基础状态")
    prop_count = store_state_family("prop", "prop", pr_state, pr, base_field="base_description", legacy_field="description", title="道具", fallback_label="基础状态", extra_description=prop_visual_content)
    look_count = store_state_family("character", "char", ch_state, ch, base_field="base_appearance", legacy_field="appearance", title="角色", fallback_label="剧本当前造型")

    print(f"=== 抽取完成：角色造型 {look_count}｜场景状态 {scene_count}｜道具状态 {prop_count} ===")
    print("下一步：造型页「① 锁定视觉词典」细化 → 「② 生成缺失造型图」；分镜页「AI 拆镜头」")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pid")
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--model", default="glm-4.6")
    a = ap.parse_args()
    globals()["BASE"] = a.base.rstrip("/") + "/api/v1"
    run(a.pid, a.model)
