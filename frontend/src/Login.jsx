/* Вход в веб-панель. Демо-роли показывают работу RBAC-изоляции. */
import { useState } from 'react';
import { api } from './api';
import { IcoShield } from './icons';

const DEMO = [
  { email: 'admin@vko.edu.kz', password: 'admin123', title: 'Администратор УО', role: 'admin' },
  { email: 'operator@vko.edu.kz', password: 'operator123', title: 'Оператор мониторинга', role: 'operator' },
  { email: 'school@vko.edu.kz', password: 'school123', title: 'Ответственный школы', role: 'school' },
  { email: 'provider@kaztelecom.kz', password: 'provider123', title: 'Поставщик связи', role: 'provider' },
];

export default function Login({ onSuccess }) {
  const [email, setEmail] = useState('admin@vko.edu.kz');
  const [password, setPassword] = useState('admin123');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (event, creds) => {
    event?.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api.login(creds?.email ?? email, creds?.password ?? password);
      onSuccess();
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  };

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={submit}>
        <div className="partner-logos lg">
          <img src="/logos/akimat.png" alt="Акимат ВКО" />
          <img src="/logos/oskemen.png" alt="Oskemen Hub" />
          <img src="/logos/uct.png" alt="Цифровой ВКО" />
        </div>
        <div className="eyebrow" style={{ textAlign: 'center' }}>
          Система автономного мониторинга · ВКО
        </div>
        <h2 style={{ textAlign: 'center' }}>Вход в систему</h2>
        <p style={{ textAlign: 'center' }}>Контроль качества интернет-соединения организаций
           образования Восточно-Казахстанской области.</p>

        {error ? <div className="login-error">{error}</div> : null}

        <div className="field">
          <label htmlFor="email">Электронная почта</label>
          <input id="email" type="email" value={email} autoComplete="username"
            onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="password">Пароль</label>
          <input id="password" type="password" value={password} autoComplete="current-password"
            onChange={(e) => setPassword(e.target.value)} />
        </div>

        <button className="btn accent" type="submit" disabled={busy}
          style={{ width: '100%', padding: 14, marginTop: 8 }}>
          {busy ? 'Проверка…' : 'Войти'}
        </button>

        <div className="demo-list">
          <div className="eyebrow" style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
            <IcoShield size={13} />Демо-доступы · роли и области видимости
          </div>
          {DEMO.map((account) => (
            <button type="button" key={account.email} className="demo-btn"
              onClick={(event) => {
                setEmail(account.email);
                setPassword(account.password);
                submit(event, account);
              }}>
              <b>{account.title}</b>
              <span className="role">{account.role}</span>
            </button>
          ))}
        </div>
      </form>
    </div>
  );
}
