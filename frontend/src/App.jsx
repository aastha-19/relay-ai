import React, { useState, useEffect, useRef } from 'react';
import './App.css';

// Base API URL with fallback
const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

function generateSessionId() {
  const rand = Math.random().toString(36).substring(2, 8);
  return `s-${rand}`;
}

export default function App() {
  // A new session starts on each page load
  const [sessionId, setSessionId] = useState(() => generateSessionId());
  const [systemPrompt, setSystemPrompt] = useState('');
  const [inputMessage, setInputMessage] = useState('');
  const [messages, setMessages] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [healthInfo, setHealthInfo] = useState(null);
  const [simulateFailure, setSimulateFailure] = useState('none');
  const messagesEndRef = useRef(null);

  // Poll/fetch health status on mount
  useEffect(() => {
    fetch(`${API_BASE_URL}/health`)
      .then((res) => {
        if (!res.ok) throw new Error(`Status ${res.status}`);
        return res.json();
      })
      .then((data) => setHealthInfo(data))
      .catch((err) => {
        console.warn('Health check failed:', err);
        setHealthInfo({ status: 'error', error: err.message });
      });
  }, []);

  // Auto-scroll to bottom of chat
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isLoading]);

  const handleSendMessage = async (e) => {
    if (e) e.preventDefault();
    const trimmed = inputMessage.trim();
    if (!trimmed || isLoading) return;

    // Immediately clear chat input
    setInputMessage('');

    // Append user message to conversation list
    const userMsg = {
      role: 'user',
      content: trimmed,
      id: Date.now() + '-user'
    };
    setMessages((prev) => [...prev, userMsg]);
    setIsLoading(true);

    try {
      const headers = {
        'Content-Type': 'application/json'
      };

      if (simulateFailure !== 'none') {
        headers['X-Simulate-Failure'] = simulateFailure;
      }

      const payload = {
        session_id: sessionId,
        message: trimmed
      };

      if (systemPrompt.trim()) {
        payload.system_prompt = systemPrompt.trim();
      }

      const res = await fetch(`${API_BASE_URL}/chat`, {
        method: 'POST',
        headers,
        body: JSON.stringify(payload)
      });

      const data = await res.json();

      if (!res.ok) {
        // Handle backend errors (422, 503, etc.) as readable chat messages
        let errorDetail = 'Unknown error';
        if (data && data.detail) {
          if (typeof data.detail === 'string') {
            errorDetail = data.detail;
          } else {
            errorDetail = JSON.stringify(data.detail);
          }
        }
        setMessages((prev) => [
          ...prev,
          {
            role: 'error',
            content: `Error [${res.status}]: ${errorDetail}`,
            id: Date.now() + '-error'
          }
        ]);
      } else {
        // Append assistant reply with metadata
        setMessages((prev) => [
          ...prev,
          {
            role: 'assistant',
            content: data.reply,
            metadata: data.metadata,
            id: Date.now() + '-assistant'
          }
        ]);
      }
    } catch (err) {
      // Network or unhandled failure shown as readable message
      setMessages((prev) => [
        ...prev,
        {
          role: 'error',
          content: `Connection Error: ${err.message || 'Unable to connect to backend service.'}`,
          id: Date.now() + '-net-error'
        }
      ]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  const startNewSession = () => {
    setSessionId(generateSessionId());
    setMessages([]);
  };

  return (
    <div className="chat-app">
      {/* Top Header */}
      <header className="chat-header">
        <div className="header-left">
          <div className="logo-badge">RelayAI</div>
          <div className="session-tag">
            Session: <code>{sessionId}</code>
          </div>
        </div>

        <div className="header-right">
          {healthInfo && (
            <div className={`health-badge ${healthInfo.status === 'ok' ? 'ok' : 'err'}`}>
              <span className="dot"></span>
              {healthInfo.status === 'ok'
                ? `Ready (${healthInfo.keys_configured} keys, Cache ${healthInfo.cache_enabled ? 'ON' : 'OFF'})`
                : 'Backend Offline'}
            </div>
          )}
          <button className="new-session-btn" onClick={startNewSession}>
            New Session
          </button>
        </div>
      </header>

      {/* Control / Settings Bar */}
      <div className="control-bar">
        <div className="system-prompt-container">
          <label htmlFor="sys-prompt">System Prompt:</label>
          <input
            id="sys-prompt"
            data-testid="system-prompt-input"
            className="system-prompt-input"
            type="text"
            placeholder="Optional system prompt (e.g. You are a concise assistant)"
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
          />
        </div>

        <div className="simulate-container">
          <label htmlFor="sim-failure">X-Simulate-Failure:</label>
          <select
            id="sim-failure"
            className="sim-failure-select"
            value={simulateFailure}
            onChange={(e) => setSimulateFailure(e.target.value)}
          >
            <option value="none">None (Normal)</option>
            <option value="key_1=429">key_1=429 (Fallback to key_2)</option>
            <option value="key_1=500">key_1=500 (Fallback to key_2)</option>
            <option value="*=429">*=429 (All keys fail -&gt; 503)</option>
          </select>
        </div>
      </div>

      {/* Messages Stream */}
      <main className="messages-stream">
        {messages.length === 0 && (
          <div className="empty-state">
            <h3>Start a new conversation</h3>
            <p>Responses feature automatic key failover and response caching.</p>
          </div>
        )}

        {messages.map((msg) => {
          if (msg.role === 'user') {
            return (
              <div key={msg.id} className="message-wrapper user-wrapper">
                <div className="user-message">
                  <div className="message-text">{msg.content}</div>
                </div>
              </div>
            );
          }

          if (msg.role === 'error') {
            return (
              <div key={msg.id} className="message-wrapper error-wrapper">
                <div className="error-message">
                  <span className="error-icon">⚠️</span>
                  <div className="message-text">{msg.content}</div>
                </div>
              </div>
            );
          }

          // Assistant Message Container
          return (
            <div
              key={msg.id}
              data-testid="assistant-message"
              className="message-wrapper assistant-wrapper"
            >
              <div className="assistant-bubble">
                <div className="message-text">{msg.content}</div>

                {msg.metadata && (
                  <div className="metadata-panel">
                    <div className="meta-item">
                      <span className="meta-label">Source:</span>
                      <span
                        data-testid="meta-source"
                        className={`meta-source-badge ${
                          msg.metadata.resolution_source === 'LLM_CACHE' ? 'cache' : 'live'
                        }`}
                      >
                        {msg.metadata.resolution_source}
                      </span>
                    </div>

                    <div className="meta-item">
                      <span className="meta-label">Model:</span>
                      <span data-testid="meta-model" className="meta-value">
                        {msg.metadata.model}
                      </span>
                    </div>

                    <div className="meta-item">
                      <span className="meta-label">Latency:</span>
                      <span data-testid="meta-latency" className="meta-value">
                        {msg.metadata.latency_ms} ms
                      </span>
                    </div>

                    <div className="meta-item">
                      <span className="meta-label">In Tokens:</span>
                      <span data-testid="meta-input-tokens" className="meta-value">
                        {msg.metadata.input_tokens}
                      </span>
                    </div>

                    <div className="meta-item">
                      <span className="meta-label">Out Tokens:</span>
                      <span data-testid="meta-output-tokens" className="meta-value">
                        {msg.metadata.output_tokens}
                      </span>
                    </div>

                    {msg.metadata.key_id && (
                      <div className="meta-item">
                        <span className="meta-label">Key:</span>
                        <span className="meta-key">{msg.metadata.key_id}</span>
                      </div>
                    )}

                    {msg.metadata.fallback_attempts > 0 && (
                      <div className="meta-item">
                        <span className="meta-label">Fallbacks:</span>
                        <span className="meta-fallback">{msg.metadata.fallback_attempts}</span>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        })}

        {isLoading && (
          <div className="loading-indicator">
            <span className="pulse-dot"></span>
            <span className="pulse-dot"></span>
            <span className="pulse-dot"></span>
            <span>Thinking...</span>
          </div>
        )}

        <div ref={messagesEndRef} />
      </main>

      {/* Input Section */}
      <footer className="input-section">
        <form className="input-form" onSubmit={handleSendMessage}>
          <textarea
            data-testid="chat-input"
            className="chat-textarea"
            placeholder="Type your message... (Press Enter to send)"
            value={inputMessage}
            onChange={(e) => setInputMessage(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={1}
            disabled={isLoading}
          />
          <button
            type="submit"
            data-testid="send-button"
            className="send-button"
            disabled={isLoading || !inputMessage.trim()}
          >
            Send
          </button>
        </form>
      </footer>
    </div>
  );
}
