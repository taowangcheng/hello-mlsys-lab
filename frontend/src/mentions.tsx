import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type RefObject,
} from 'react';
import { createPortal } from 'react-dom';
import { AtSign, Check } from 'lucide-react';
import { t } from './api';

export type Reference = {
  key: string;
  title: string;
  kind: string;
  detail: string;
  search: string;
};

const referencePattern =
  /@((?:\d\.\d-\d+\/)?(?:r\d+(?:\/[A-Za-z0-9_-]+)?|m\d+|(?:sim|hw)_\d+|overview|report))(?![A-Za-z0-9_/-])/g;

export function inlineReferences(text: string, session: string) {
  return [
    ...new Set(
      [...text.matchAll(referencePattern)].map((match) =>
        /^\d\.\d-\d+\//.test(match[1]) ? match[1] : `${session}/${match[1]}`,
      ),
    ),
  ];
}

export function withoutReference(text: string, key: string, session: string) {
  return text.replace(referencePattern, (token, id: string) =>
    id === key || `${session}/${id}` === key ? '' : token,
  );
}

export function matchesReference(item: Reference, query: string) {
  return `${item.title} ${item.kind} ${item.detail} ${item.search}`
    .toLowerCase()
    .includes(query.trim().toLowerCase());
}

type Mention = { start: number; end: number; query: string };

function atCursor(
  input: HTMLTextAreaElement,
  options: Reference[],
  session: string,
): Mention | null {
  const cursor = input.selectionStart;
  if (cursor !== input.selectionEnd) return null;
  const before = input.value.slice(0, cursor);
  const start = before.lastIndexOf('@');
  if (start < 0 || /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/.test(before)) return null;
  const query = before.slice(start + 1);
  if (!/^[\p{L}\p{N}_./-]*$/u.test(query)) return null;
  const full = input.value.slice(start + 1).match(/^[A-Za-z0-9_./-]+/)?.[0];
  const existing =
    full && options.some((item) => item.key === full || item.key === `${session}/${full}`);
  return { start, end: existing ? start + 1 + full.length : cursor, query };
}

function popupPosition(input: HTMLTextAreaElement): CSSProperties {
  const rectangle = input.getBoundingClientRect();
  const computed = getComputedStyle(input);
  const mirror = document.createElement('div');
  for (const property of [
    'box-sizing',
    'font-family',
    'font-size',
    'font-weight',
    'font-style',
    'line-height',
    'letter-spacing',
    'padding',
    'border-width',
    'text-indent',
    'tab-size',
  ])
    mirror.style.setProperty(property, computed.getPropertyValue(property));
  Object.assign(mirror.style, {
    position: 'fixed',
    visibility: 'hidden',
    whiteSpace: 'pre-wrap',
    overflowWrap: 'break-word',
    width: `${rectangle.width}px`,
    left: `${rectangle.left}px`,
    top: `${rectangle.top}px`,
  });
  mirror.textContent = input.value.slice(0, input.selectionStart);
  const marker = document.createElement('span');
  marker.textContent = input.value[input.selectionStart] || '\u200b';
  mirror.append(marker);
  document.body.append(mirror);
  const caret = marker.getBoundingClientRect();
  mirror.remove();
  const viewport = window.visualViewport;
  const top = viewport?.offsetTop || 0;
  const left = viewport?.offsetLeft || 0;
  const width = Math.min(370, (viewport?.width || innerWidth) - 24);
  const bottom = top + (viewport?.height || innerHeight);
  const caretTop = Math.max(rectangle.top, Math.min(rectangle.bottom, caret.top - input.scrollTop));
  const caretBottom = Math.min(
    rectangle.bottom,
    caretTop + (parseFloat(computed.lineHeight) || 22),
  );
  const above = caretTop - top > bottom - caretBottom;
  return {
    position: 'fixed',
    width,
    left: Math.max(
      left + 12,
      Math.min(caret.left - input.scrollLeft, left + (viewport?.width || innerWidth) - width - 12),
    ),
    ...(above ? { bottom: innerHeight - caretTop + 6 } : { top: caretBottom + 6 }),
    maxHeight: Math.max(100, Math.min(340, (above ? caretTop - top : bottom - caretBottom) - 18)),
  };
}

export function MentionComposer({
  value,
  onChange,
  onSend,
  inputRef,
  options,
  selected,
  session,
}: {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  inputRef: RefObject<HTMLTextAreaElement | null>;
  options: Reference[];
  selected: string[];
  session: string;
}) {
  const [mention, setMention] = useState<Mention | null>(null);
  const [active, setActive] = useState(0);
  const [position, setPosition] = useState<CSSProperties>({});
  const composing = useRef(false);
  const dismissed = useRef('');
  const pendingCursor = useRef<number | null>(null);
  const selectionChanged = useRef<() => void>(() => {});
  const popup = useRef<HTMLDivElement>(null);
  const listId = useId();
  const matches = mention ? options.filter((item) => matchesReference(item, mention.query)) : [];
  const index = Math.min(active, Math.max(0, matches.length - 1));
  const keyOf = (item: Mention) => `${item.start}:${item.query}`;
  const unavailable = (item: Reference) => selected.length >= 12 && !selected.includes(item.key);

  function update(input: HTMLTextAreaElement) {
    const next = composing.current ? null : atCursor(input, options, session);
    if (!next || keyOf(next) === dismissed.current) {
      setMention(null);
      return;
    }
    if (!mention || keyOf(next) !== keyOf(mention)) setActive(0);
    setMention(next);
    setPosition(popupPosition(input));
  }
  selectionChanged.current = () => {
    const input = inputRef.current;
    if (input && document.activeElement === input) update(input);
  };

  useEffect(() => {
    const input = inputRef.current;
    const selected = () => selectionChanged.current();
    input?.addEventListener('select', selected);
    document.addEventListener('selectionchange', selected);
    return () => {
      input?.removeEventListener('select', selected);
      document.removeEventListener('selectionchange', selected);
    };
  }, [inputRef, session]);

  function close() {
    if (mention) dismissed.current = keyOf(mention);
    setMention(null);
  }

  function choose(item: Reference) {
    if (!mention || unavailable(item)) return;
    const token = `@${item.key.slice(session.length + 1)}`;
    const suffix = value.slice(mention.end);
    const updated =
      value.slice(0, mention.start) + token + (/^\s/.test(suffix) ? '' : ' ') + suffix;
    pendingCursor.current = mention.start + token.length + 1;
    dismissed.current = '';
    setMention(null);
    onChange(updated);
  }

  useLayoutEffect(() => {
    if (pendingCursor.current !== null) {
      const cursor = pendingCursor.current;
      pendingCursor.current = null;
      inputRef.current?.focus();
      inputRef.current?.setSelectionRange(cursor, cursor);
    }
  });

  useEffect(() => {
    setMention(null);
    dismissed.current = '';
  }, [session]);
  useEffect(() => {
    if (!mention) return;
    const move = () => inputRef.current && setPosition(popupPosition(inputRef.current));
    window.addEventListener('resize', move);
    window.addEventListener('scroll', move, true);
    window.visualViewport?.addEventListener('resize', move);
    window.visualViewport?.addEventListener('scroll', move);
    return () => {
      window.removeEventListener('resize', move);
      window.removeEventListener('scroll', move, true);
      window.visualViewport?.removeEventListener('resize', move);
      window.visualViewport?.removeEventListener('scroll', move);
    };
  }, [mention]);
  useEffect(() => {
    popup.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [index, mention?.query]);

  return (
    <>
      <textarea
        ref={inputRef}
        aria-label={t('Message')}
        aria-autocomplete="list"
        aria-controls={mention ? listId : undefined}
        aria-activedescendant={mention && matches.length ? `${listId}-${index}` : undefined}
        rows={3}
        placeholder={t('Ask a question, describe an experiment, or type @ to add a result…')}
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
          update(event.target);
        }}
        onSelect={(event) => update(event.currentTarget)}
        onPointerDown={() => {
          dismissed.current = '';
        }}
        onBlur={() => setMention(null)}
        onCompositionStart={() => {
          composing.current = true;
          setMention(null);
        }}
        onCompositionEnd={(event) => {
          composing.current = false;
          update(event.currentTarget);
        }}
        onKeyDown={(event) => {
          if (composing.current || event.nativeEvent.isComposing || event.keyCode === 229) return;
          if (mention) {
            if (event.key === 'Escape') {
              event.preventDefault();
              event.stopPropagation();
              close();
              return;
            }
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
              event.preventDefault();
              if (matches.length)
                setActive(
                  (index + (event.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length,
                );
              return;
            }
            if ((event.key === 'Enter' && !event.shiftKey) || event.key === 'Tab') {
              if (event.key === 'Tab' && matches[index] && unavailable(matches[index])) {
                close();
                return;
              }
              if (matches.length || event.key === 'Enter') event.preventDefault();
              if (matches[index]) choose(matches[index]);
              else close();
              return;
            }
          }
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            onSend();
          }
        }}
      />
      {mention &&
        createPortal(
          <div
            className="mention-popover"
            ref={popup}
            style={position}
            onPointerDown={(event) => event.preventDefault()}
          >
            <div className="mention-heading">
              <AtSign size={15} />
              <strong>{t('Reference a record')}</strong>
            </div>
            <div
              className="mention-options"
              id={listId}
              role="listbox"
              aria-label={t('Matching records')}
            >
              {matches.map((item, number) => (
                <button
                  key={item.key}
                  id={`${listId}-${number}`}
                  type="button"
                  role="option"
                  aria-selected={index === number}
                  aria-disabled={unavailable(item)}
                  onPointerMove={() => setActive(number)}
                  onClick={() => choose(item)}
                  tabIndex={-1}
                >
                  <span>
                    <strong>{item.title}</strong>
                    <small>
                      {item.kind} · {item.detail}
                    </small>
                  </span>
                  {selected.includes(item.key) && <Check size={14} aria-label={t('Attached')} />}
                </button>
              ))}
            </div>
            {!matches.length && (
              <p className="mention-empty" role="status">
                {t('No matching records in this conversation.')}
              </p>
            )}
            <div className="mention-help" role="status">
              {selected.length >= 12
                ? t('Up to 12 records per message. Remove one to add another.')
                : t('↑ ↓ to choose · Enter or Tab to add · Esc to close')}
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}
