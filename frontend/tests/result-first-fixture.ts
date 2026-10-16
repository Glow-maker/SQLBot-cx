export const drawings: any[] = []
export const assistant = { getAssistant: false, getEmbedded: false }
export const useAssistantStore = () => assistant
export const useI18n = () => ({ t: (key: string) => key })
export const useClipboard = () => ({ copy: async () => {} })
export const i18n = { global: { t: (key: string) => key } }
export const S2Event = {}
export class TableSheet {}
export const copyToClipboard = async () => {}
export const useEmitt = () => {}
export default async () => ({})
export function getChartInstance(type: string, id: string) {
  const instance = { type, id, data: undefined as any, axis: undefined as any, renders: 0, destroyed: false,
    init(axis: any, data: any) { this.axis = axis; this.data = data },
    render() { this.renders++ }, destroy() { this.destroyed = true },
  }
  drawings.push(instance)
  return instance
}
