'use client';

import { memo, useCallback, useState, type FormEvent } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import type { AppDispatch, RootState } from '@/store';
import { loginThunk, registerThunk } from '@/store/slices/authSlice';
import { showToast } from '@/store/slices/uiSlice';
import { getApiBase, setApiBase } from '@/lib/api';

type AuthTab = 'login' | 'register';

const PLACEHOLDERS = {
  username: '通行证 / 用户名',
  email: '电子邮箱',
  password: '密钥 / 密码',
};

function AuthOverlay() {
  const dispatch = useDispatch<AppDispatch>();
  const visible = useSelector((state: RootState) => !(state.auth.token && state.auth.user));
  const submitting = useSelector((state: RootState) => state.auth.submitting);

  const [tab, setTab] = useState<AuthTab>('login');
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [apiBase, setApiBaseInput] = useState<string>(() => getApiBase());

  const handleSwitchTab = useCallback((next: AuthTab) => {
    setTab(next);
  }, []);

  const handleSubmit = useCallback(
    async (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      if (!username.trim() || !password) return;

      setApiBase(apiBase.trim() || getApiBase());

      try {
        if (tab === 'register') {
          await dispatch(registerThunk({ username: username.trim(), email, password })).unwrap();
          dispatch(showToast({ message: '欢迎加入知行！' }));
        } else {
          await dispatch(loginThunk({ username: username.trim(), password })).unwrap();
          dispatch(showToast({ message: '欢迎回来！' }));
        }
        setPassword('');
      } catch (err) {
        // unwrap() 可能抛出普通对象；按字段提取后端 msg，不能仅判断 Error 实例。
        // 用 instanceof 判类型会全部落空、真实文案被吞成兜底文案，故直接按字段取；
        // msg 是后端统一错误体 {code,msg,data} 的字段，缺它就会重新掉回"操作失败"
        const message =
          (err as { detail?: string })?.detail ||
          (err as { msg?: string })?.msg ||
          (err as { message?: string })?.message ||
          '操作失败';
        dispatch(showToast({ message, type: 'error' }));
      }
    },
    [apiBase, dispatch, email, password, tab, username],
  );

  return (
    <div
      className={`fixed inset-0 z-[100] flex items-center justify-center bg-[rgba(26,77,84,0.3)] backdrop-blur-overlay transition-opacity duration-300 ${
        visible ? '' : 'hidden'
      }`}
    >
      <div className="glass-card relative w-[400px] overflow-hidden rounded-lg bg-white/95 p-10">
        <div className="pointer-events-none absolute -right-5 -top-5 flex h-[100px] w-[100px] rotate-20 items-center justify-center rounded-full border-2 border-dashed border-accent font-display text-accent opacity-20">
          <span className="font-bold">VISA</span>
        </div>

        <div className="mb-[30px] text-center">
          <div className="mb-2 font-serif text-4xl font-bold tracking-[4px] text-primary">
            <i className="fa-solid fa-compass mr-2 text-2xl text-accent" />
            知行
          </div>
          <p className="font-display italic text-text-light">
            Read Thousands of Books, Travel Thousands of Miles
          </p>
        </div>

        <div className="mb-6 flex border-b border-[#ddd]">
          {(['login', 'register'] as const).map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => handleSwitchTab(key)}
              className={`relative flex-1 cursor-pointer border-none bg-transparent py-3 text-[15px] ${
                tab === key
                  ? 'font-semibold text-primary after:absolute after:-bottom-px after:left-0 after:h-[2px] after:w-full after:bg-primary after:content-[""]'
                  : 'text-text-light'
              }`}
            >
              {key === 'login' ? '登 录' : '注 册'}
            </button>
          ))}
        </div>

        <form onSubmit={handleSubmit}>
          <div className="mb-4">
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder={PLACEHOLDERS.username}
              required
              className="w-full rounded-[4px] border border-[#ddd] bg-white p-3 transition-all focus:border-primary focus:shadow-[0_0_0_2px_rgba(26,77,84,0.1)] focus:outline-none"
            />
          </div>

          {tab === 'register' && (
            <div className="mb-4">
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder={PLACEHOLDERS.email}
                required={tab === 'register'}
                className="w-full rounded-[4px] border border-[#ddd] bg-white p-3 transition-all focus:border-primary focus:shadow-[0_0_0_2px_rgba(26,77,84,0.1)] focus:outline-none"
              />
            </div>
          )}

          <div className="mb-4">
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={PLACEHOLDERS.password}
              required
              className="w-full rounded-[4px] border border-[#ddd] bg-white p-3 transition-all focus:border-primary focus:shadow-[0_0_0_2px_rgba(26,77,84,0.1)] focus:outline-none"
            />
          </div>

          <button
            type="submit"
            disabled={submitting}
            className="w-full cursor-pointer rounded-[4px] bg-primary p-3.5 text-[15px] text-white transition-colors duration-300 hover:bg-primary-light disabled:cursor-not-allowed disabled:bg-[#ccc]"
          >
            {submitting ? (
              <>
                <i className="fa-solid fa-spinner fa-spin" /> 验证中...
              </>
            ) : tab === 'register' ? (
              '注册通行证'
            ) : (
              '开启旅程'
            )}
          </button>
        </form>

        <details className="mt-5 text-center text-xs">
          <summary className="cursor-pointer text-text-light">连接设置</summary>
          <input
            type="text"
            value={apiBase}
            placeholder="留空使用本站后端代理"
            onChange={(e) => setApiBaseInput(e.target.value)}
            onBlur={() => setApiBase(apiBase.trim() || getApiBase())}
            className="mt-1 w-4/5 rounded border border-[#ddd] p-1 text-center focus:border-primary focus:outline-none"
          />
        </details>
      </div>
    </div>
  );
}

export default memo(AuthOverlay);
