'use client';

import { useEffect, type ReactNode } from 'react';
import { Provider } from 'react-redux';
import { restorePersistedState, store } from '@/store';

/**
 * 全局状态 Provider：
 * - Redux Provider 注入 store
 * - 使用自定义持久化层（store 创建时同步 rehydrate），无需 PersistGate
 */
export default function Providers({ children }: { children: ReactNode }) {
  useEffect(() => { restorePersistedState(); }, []);
  return <Provider store={store}>{children}</Provider>;
}
