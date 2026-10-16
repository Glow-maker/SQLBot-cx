import { request } from '@/utils/request'

export interface QuestionConstraint {
  code: string
  name: string
  content: string
  enabled: boolean
}

export const constraintsApi = {
  list: () => request.get<QuestionConstraint[]>('/operations/constraints'),
  update: (code: string, data: Pick<QuestionConstraint, 'content' | 'enabled'>) =>
    request.put<QuestionConstraint>(`/operations/constraints/${encodeURIComponent(code)}`, data),
}
