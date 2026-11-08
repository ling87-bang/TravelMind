/** skill_name 必须与后端一致；扩展技能时在此注册对应组件。 */
import TrainMultiSegment from './TrainMultiSegment';
import HotelMultiSegment from '../hotel-multi-segment/HotelMultiSegment';
// 多段航班 Skill（组件实现放同级 flight-multi-segment 目录）
import FlightMultiSegment from '../flight-multi-segment/FlightMultiSegment';
// 多段自驾 Skill（组件实现放同级 driving-multi-segment 目录）
import DrivingMultiSegment from '../driving-multi-segment/DrivingMultiSegment';

import { HOTEL_MULTI_SEGMENT_SKILL, TRAIN_MULTI_SEGMENT_SKILL } from './types';
import { FLIGHT_MULTI_SEGMENT_SKILL } from '../flight-multi-segment/types';
import { DRIVING_MULTI_SEGMENT_SKILL } from '../driving-multi-segment/types';

/** 所有 UI Skill 组件的统一 props 形态：后端 payload + 全局提交回调 */
export interface UiSkillProps<TData, TPayload> {
  /** 后端 Skill 出参（结构化 payload，字段与后端 Pydantic 模型严格一致） */
  data: TData;
  /** 提交回调：仅在用户完成全部子步骤并确认时触发一次 */
  onSubmit: (payload: TPayload) => void;
  /** 提交中标记（由上层控制，用于置灰提交按钮） */
  submitting?: boolean;
  /** 已提交标记：卡片提交成功后保留展示并锁定交互（上一步/提交均禁用） */
  submitted?: boolean;
  /** 各段选中 id 账本（提交成功后由上层记住，切回会话时复原已选状态） */
  initialSelections?: Record<string, string>;
}

export type RegisteredSkillComponent =
  (typeof UI_SKILL_REGISTRY)[keyof typeof UI_SKILL_REGISTRY];

export const UI_SKILL_REGISTRY = {
  [TRAIN_MULTI_SEGMENT_SKILL]: TrainMultiSegment,
  [HOTEL_MULTI_SEGMENT_SKILL]: HotelMultiSegment,
  [FLIGHT_MULTI_SEGMENT_SKILL]: FlightMultiSegment,
  [DRIVING_MULTI_SEGMENT_SKILL]: DrivingMultiSegment,
} as const;