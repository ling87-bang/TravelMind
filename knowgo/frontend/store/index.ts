/** 使用自定义持久化层同步鉴权与会话状态。 */
import { combineReducers, configureStore } from '@reduxjs/toolkit';
import authReducer, {
  logoutThunk,
  rehydrateAuth,
} from './slices/authSlice';
import chatReducer, { rehydrateChat } from './slices/chatSlice';
import uiReducer, { showToast } from './slices/uiSlice';
import { setToken, setUnauthorizedHandler } from '@/lib/api';
import {
  loadPersistedAuth,
  loadPersistedChat,
  persistAuth,
  persistChat,
} from '@/lib/persist';

const rootReducer = combineReducers({
  auth: authReducer,
  chat: chatReducer,
  ui: uiReducer,
});

export const store = configureStore({
  reducer: rootReducer,
  devTools: process.env.NODE_ENV !== 'production',
  middleware: (getDefaultMiddleware) =>
    getDefaultMiddleware({
      serializableCheck: {
        ignoredActions: ['persist/PERSIST', 'persist/REHYDRATE'],
      },
    }),
});


const isClient = typeof window !== 'undefined';

let persistenceReady = false;

/** 在 React 首次挂载后恢复，保证 SSR 与首帧客户端状态一致。 */
export function restorePersistedState(): void {
  if (!isClient || persistenceReady) return;
  const auth = loadPersistedAuth();
  const chat = loadPersistedChat();
  persistenceReady = true;
  if (auth?.token) {
    store.dispatch(rehydrateAuth(auth));
  }
  if (chat?.conversations.length) {
    store.dispatch(rehydrateChat(chat));
  }
}


// token 与请求层双向同步；auth/chat 自动持久化
store.subscribe(() => {
  const state = store.getState();
  setToken(state.auth.token);

  if (isClient && persistenceReady) {
    persistAuth({ token: state.auth.token, user: state.auth.user });
    persistChat({
      conversations: state.chat.conversations,
      currentConversationId: state.chat.currentConversationId,
    });
  }
});

// 401 全局鉴权失效处理：自动登出 + 全局 Toast
setUnauthorizedHandler(() => {
  store.dispatch(logoutThunk());
  store.dispatch(showToast({ message: '登录已过期，请重新登录', type: 'error' }));
});

export type RootState = ReturnType<typeof rootReducer>;
export type AppDispatch = typeof store.dispatch;
