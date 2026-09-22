import React from 'react';

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error('Unhandled UI error caught by ErrorBoundary:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          minHeight: '100vh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '24px',
          background: '#F6F8FB',
          color: '#1A2233',
          fontFamily: 'system-ui, -apple-system, sans-serif',
          textAlign: 'center',
        }}>
          <div style={{
            background: '#FFFFFF',
            border: '1px solid #E2E6EE',
            borderRadius: '16px',
            padding: '32px',
            maxWidth: '520px',
            width: '100%',
            boxShadow: '0 12px 32px rgba(0,0,0,0.06)',
          }}>
            <div style={{
              width: '48px',
              height: '48px',
              borderRadius: '50%',
              background: '#FEE4E2',
              color: '#D92D20',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              margin: '0 auto 16px',
              fontSize: '24px',
              fontWeight: 'bold',
            }}>
              !
            </div>
            <h2 style={{ margin: '0 0 8px', fontSize: '20px', fontWeight: '700' }}>
              Произошла ошибка интерфейса
            </h2>
            <p style={{ margin: '0 0 20px', fontSize: '14px', color: '#5F6D88', lineHeight: 1.5 }}>
              При отображении компонента возник сбой. Вы можете перезагрузить страницу или вернуться на главную.
            </p>
            {this.state.error?.message && (
              <pre style={{
                background: '#F2F4F7',
                padding: '12px',
                borderRadius: '8px',
                fontSize: '12px',
                color: '#344054',
                textAlign: 'left',
                overflowX: 'auto',
                marginBottom: '20px',
              }}>
                {this.state.error.message}
              </pre>
            )}
            <div style={{ display: 'flex', gap: '12px', justifyContent: 'center' }}>
              <button
                onClick={() => { this.setState({ hasError: false, error: null }); window.location.reload(); }}
                style={{
                  padding: '10px 20px',
                  background: '#2F6BF6',
                  color: '#fff',
                  border: 'none',
                  borderRadius: '10px',
                  fontWeight: '600',
                  fontSize: '14px',
                  cursor: 'pointer',
                }}>
                Перезагрузить страницу
              </button>
              <button
                onClick={() => {
                  this.setState({ hasError: false, error: null });
                  window.location.href = window.location.pathname;
                }}
                style={{
                  padding: '10px 20px',
                  background: '#EAECF0',
                  color: '#344054',
                  border: 'none',
                  borderRadius: '10px',
                  fontWeight: '600',
                  fontSize: '14px',
                  cursor: 'pointer',
                }}>
                На главную
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
