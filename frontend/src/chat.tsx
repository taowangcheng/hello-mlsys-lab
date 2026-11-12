import { useEffect, useRef, useState } from 'react';
import { ArrowUp, MessageSquare, PanelRightClose, Square, X } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import { api, t, type Data, type Workspace } from './api';
import { Button, ErrorLine } from './ui';

export function Chat({
  workspace,
  refs,
  setRefs,
  onClose,
  onNavigate,
}: {
  workspace: Workspace;
  refs: string[];
  setRefs: (keys: string[]) => void;
  onClose: () => void;
  onNavigate: (key: string) => void;
}) {
  const session = refs[0]?.split('/')[0] || workspace.session;
  const [messages, setMessages] = useState<Data[]>([]);
  const [status, setStatus] = useState('offline');
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const [sending, setSending] = useState(false);
  const scroll = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  useEffect(() => {
    let current = true;
    const load = () =>
      api(`/api/sessions/${session}/chat?limit=100`)
        .then((result) => {
          if (current) {
            setMessages(result.entries);
            setStatus(result.agent.status);
          }
        })
        .catch((error) => {
          if (current) setError(error.message);
        });
    setMessages([]);
    load();
    const timer = setInterval(load, 1400);
    return () => {
      current = false;
      clearInterval(timer);
    };
  }, [session]);
  useEffect(() => {
    if (scroll.current && follow.current) scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [messages]);
  async function send() {
    if (!text.trim()) return;
    setSending(true);
    setError('');
    follow.current = true;
    try {
      await api(`/api/workbench/sessions/${session}/chat`, { text, refs });
      setText('');
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setSending(false);
    }
  }
  const visible = messages.filter((message) =>
    ['student', 'agent', 'error'].includes(message.role),
  );
  return (
    <aside className="discussion">
      <div className="discussion-header">
        <span>
          <MessageSquare size={17} />
          <strong>{t('Experiment discussion')}</strong>
        </span>
        <button className="icon-button" onClick={onClose} aria-label={t('Close discussion')}>
          <PanelRightClose size={17} />
        </button>
      </div>
      <div className="discussion-context">
        <span className="eyebrow">{t('DISCUSSING')}</span>
        {refs.length ? (
          <div className="reference-chips">
            {refs.map((key) => (
              <span key={key}>
                <button onClick={() => onNavigate(key)}>
                  {workspace.runs.find((run) => run.key === key)?.candidate ||
                    workspace.experiments.find((item) => item.key === key)?.title ||
                    key.split('/').slice(1).join('/')}
                </button>
                <button
                  aria-label={t('Remove reference')}
                  onClick={() => setRefs(refs.filter((item) => item !== key))}
                >
                  <X size={11} />
                </button>
              </span>
            ))}
          </div>
        ) : (
          <p>{t('This task. Select a result to ask about its evidence.')}</p>
        )}
      </div>
      <div
        className="discussion-messages"
        ref={scroll}
        onScroll={(event) => {
          const node = event.currentTarget;
          follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 80;
        }}
      >
        {!visible.length && (
          <div className="discussion-empty">
            <div className="assistant-mark">m</div>
            <h3>{t('A second pair of eyes')}</h3>
            <p>
              {t(
                'Ask about a result, explore an explanation, or prepare a new experiment together.',
              )}
            </p>
            <button
              onClick={() => setText(t('Help me design a controlled experiment for this task.'))}
            >
              {t('Help design an experiment')}
            </button>
            <button
              onClick={() =>
                setText(t('What evidence should I look at before choosing a configuration?'))
              }
            >
              {t('Think through the evidence')}
            </button>
          </div>
        )}
        {visible.map((message) => (
          <article className={`discussion-message ${message.role}`} key={message.id}>
            <span className="message-author">
              {t(
                message.role === 'student'
                  ? 'You'
                  : message.role === 'error'
                    ? 'Connection'
                    : 'Lab assistant',
              )}
            </span>
            <ReactMarkdown
              components={{
                a: ({ href, children }) => (
                  <a href={href} target="_blank" rel="noreferrer">
                    {children}
                  </a>
                ),
              }}
            >
              {message.text || ''}
            </ReactMarkdown>
            {message.role === 'agent' &&
              [...new Set<string>((message.text || '').match(/\b(?:sim_|hw_)\d+\b/g) || [])].map(
                (id) => (
                  <button
                    className="citation"
                    key={id}
                    onClick={() => onNavigate(`${session}/${id}`)}
                  >
                    {id}
                  </button>
                ),
              )}
          </article>
        ))}
        {['running', 'connecting'].includes(status) && (
          <div className="reply-status">
            <span className="pulse-dot" />
            {t(status === 'connecting' ? 'Connecting…' : 'Preparing a reply…')}
          </div>
        )}
      </div>
      <div className="discussion-composer">
        <ErrorLine text={error} />
        <textarea
          aria-label={t('Message')}
          rows={3}
          placeholder={t('Ask a question or describe an idea…')}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              send();
            }
          }}
        />
        <div className="composer-actions">
          <span>{t('Experiments stay under your control.')}</span>
          {['running', 'connecting'].includes(status) && (
            <Button
              className="small ghost"
              onClick={() =>
                api(`/api/sessions/${session}/agent/interrupt`, {}).catch((error) =>
                  setError(error.message),
                )
              }
            >
              <Square size={12} />
              {t('Stop reply')}
            </Button>
          )}
          <Button
            className="primary icon-button"
            aria-label={t('Send')}
            busy={sending}
            disabled={!text.trim()}
            onClick={send}
          >
            <ArrowUp size={17} />
          </Button>
        </div>
      </div>
    </aside>
  );
}
