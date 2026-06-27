// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * 桌宠可选形象清单 — 设置面板「桌宠形象」下拉读这里。
 *
 * 每个模型 = Cubism 4 .model3.json 路径（vite serve 的 /assets/live2d/...）。
 * 加新模型：解压到 public/assets/live2d/<干净无空格目录>/，确保
 * model3.json + .moc3 + textures 路径无空格/#/括号（URL 不友好字符），
 * 然后在这里加一行。
 *
 * ⚠️ license: 多数免费 Live2D 模型仅限直播/视频，不含软件产品分发。
 * 商用发布前须逐个核实授权（见各模型目录的 license 文件）。
 */
export interface PetModel {
  /** 稳定 id，用于 localStorage 持久化 + Live2DCanvas remount key。 */
  readonly id: string;
  /** 下拉框显示名。 */
  readonly name: string;
  /** /assets 下的 .model3.json 路径（无空格）。 */
  readonly modelPath: string;
}

export const PET_MODELS: readonly PetModel[] = [
  {
    id: "estella",
    name: "Estella（蓝衣）",
    modelPath: "/assets/live2d/estella/estella.model3.json",
  },
  {
    id: "hiyori",
    name: "Hiyori（校服）",
    modelPath: "/assets/live2d/hiyori/Hiyori.model3.json",
  },
];

export const DEFAULT_PET_MODEL_ID = "estella";

/** localStorage key — 记住用户上次选的形象。 */
export const PET_MODEL_LS_KEY = "deskpet_pet_model_id";

/** 按 id 在给定清单里找模型；找不到回退第一个（清单非空）。 */
export function resolvePetModel(
  models: readonly PetModel[],
  id: string | null | undefined,
): PetModel {
  return models.find((m) => m.id === id) ?? models[0] ?? PET_MODELS[0];
}

/**
 * 动态获取可用模型清单 —— fetch vite 插件实时扫出的 manifest
 * (/assets/live2d/models.json)。失败/空时回退内置 PET_MODELS。
 * 这样设置面板「桌宠形象」下拉会列出 public/assets/live2d/ 下**所有**
 * 含 .model3.json 的模型目录，加/删模型刷新即生效。
 */
export async function fetchPetModels(): Promise<PetModel[]> {
  try {
    const res = await fetch("/assets/live2d/models.json", { cache: "no-store" });
    if (!res.ok) return [...PET_MODELS];
    const data: unknown = await res.json();
    if (Array.isArray(data)) {
      const valid = data.filter(
        (m): m is PetModel =>
          !!m &&
          typeof (m as PetModel).id === "string" &&
          typeof (m as PetModel).name === "string" &&
          typeof (m as PetModel).modelPath === "string",
      );
      if (valid.length > 0) return valid;
    }
    return [...PET_MODELS];
  } catch {
    return [...PET_MODELS];
  }
}
