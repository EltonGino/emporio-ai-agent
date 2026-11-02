import { createMockClient } from './mock'
import { createRealClient } from './real'
import type { ApiClient } from './types'

export const isMock = import.meta.env.VITE_MOCK === 'true'

export function createApiClient(options: { instant?: boolean } = {}): ApiClient {
  return isMock ? createMockClient(options) : createRealClient()
}

export * from './types'
