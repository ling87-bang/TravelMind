/**
 * 多段自驾契约：字段与后端 payload 保持一致，段数动态生成。
 * 每段展示默认策略的一条路线，用户只确认，不选择路线策略。
 */


/** Agent UI Skill 注册键：上层用该字段匹配「应该渲染哪个前端组件」 */
export const DRIVING_MULTI_SEGMENT_SKILL = 'driving_multi_segment' as const;

export type DrivingMultiSegmentSkillName = typeof DRIVING_MULTI_SEGMENT_SKILL;


/** 一段自驾路线（segments 的元素）；段数由用户行程决定，动态可变 */
export interface DrivingSegment {
  /** 段唯一标识，后端按 legs 顺序生成：s1 / s2 / s3 … */
  segment_id: string;
  /** 该段出发城市 */
  from: string;
  /** 该段到达城市 */
  to: string;
  /** 该段出行日期 YYYY-MM-DD；未知时为空字符串（自驾可不带日期） */
  date: string;
  /** 该段是否查询成功：true 才可继续；false 时前端置灰该段并说明原因 */
  ok: boolean;
  /** 驾车总距离（公里）；未解析到时为 0 */
  distance_km: number;
  /** 预计耗时 HH:mm；未解析到时为空字符串 */
  duration_text: string;
  /** 过路费（元）；未解析到或免费时为 0 */
  tolls: number;
  /** 红绿灯数；未解析到时为 0 */
  traffic_lights: number;
  /** 关键路段摘要（前几条导航指令）；无则为空字符串 */
  summary: string;
}

/** 后端 Skill 出参：Agent 并行查询全部段后一次性返回 */
export interface DrivingMultiSegmentSkillData {
  skill_name: DrivingMultiSegmentSkillName;
  segments: DrivingSegment[];
}


/**
 * 子步骤状态机（按 segments.length 动态展开，不写死段数）
 *   select   逐段核对阶段：配合 stepIndex（0 … N-1）表示正在核对第 N 段（只读，无选项）
 *   preview  预览汇总页（第 N+1 步），唯一提供【提交确认】的页面
 *   submit   已提交（按钮置为已提交态）
 */
export type DrivingStage = 'select' | 'preview' | 'submit';

/** 一次性全局提交的 payload（只读确认：每段仅需 segment_id） */
export interface DrivingMultiSegmentSubmitPayload {
  skill_name: DrivingMultiSegmentSkillName;
  /** 按 segments 顺序提交，每段带 segment_id */
  segments: Array<{ segment_id: string }>;
}

/** 后端提交回执（对应 driving_skill_schema.py 的 DrivingSubmitResult） */
export interface DrivingSubmitResult {
  /** 全部段基础校验通过为 true（后端不做任何时间/路线校验） */
  ok: boolean;
  /** OK / EMPTY_SELECTION / SEGMENT_NOT_FOUND / INCOMPLETE / NO_PAYLOAD / INVALID_PAYLOAD */
  code: string;
  /** 面向用户的中文说明 */
  msg: string;
  accepted: Array<{ segment_id: string }>;
  /** 已确认各段的展示文本（后端据此拼续跑发言） */
  texts?: string[];
}