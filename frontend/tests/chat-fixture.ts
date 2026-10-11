export class Chat {}
export class ChatInfo {
  id = 1
  records: any[] = []
}
export class ChatRecord {}
export let responseFactory: (controller: AbortController) => Promise<any>
export function setResponse(factory: typeof responseFactory) {
  responseFactory = factory
}
export const questionApi = {
  add: (_: unknown, controller: AbortController) => responseFactory(controller),
}
export const chatApi = {
  analysis: (_: unknown, controller: AbortController) => responseFactory(controller),
  predict: (_: unknown, controller: AbortController) => responseFactory(controller),
  get_chart_data: async () => [],
  get_chart_predict_data: async () => [],
}
