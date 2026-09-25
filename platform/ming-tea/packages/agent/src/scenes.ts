import type { SceneId } from "@ming-tea/protocol";

export interface SceneDefinition {
  id: SceneId;
  label: string;
  prompt: string;
  tools: string[];
  riskProfile: "balanced" | "developer" | "guided";
}

export const SCENES: Record<SceneId, SceneDefinition> = {
  office: {
    id: "office",
    label: "办公模式",
    prompt: "协助处理文档、表格、演示文稿、网页和文件；涉及发送、上传或删除时先请求确认。",
    tools: ["browser", "files", "office", "terminal"],
    riskProfile: "balanced",
  },
  development: {
    id: "development",
    label: "开发模式",
    prompt: "读取项目、分析日志、运行测试并展示代码变更；特权命令和系统级修改必须先请求确认。",
    tools: ["files", "terminal", "browser", "office"],
    riskProfile: "developer",
  },
  learning: {
    id: "learning",
    label: "辅助学习模式",
    prompt: "优先解释、拆解和演示；仅在用户明确要求时执行会改变系统或外部状态的动作。",
    tools: ["browser", "files", "office", "terminal"],
    riskProfile: "guided",
  },
};
