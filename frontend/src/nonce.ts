import { setNonce } from 'get-nonce';

// Radix's scroll lock inserts a style element. Only this response's nonce permits it.
const nonce = document.querySelector('meta[name="csp-style-nonce"]')?.getAttribute('content');
if (nonce) setNonce(nonce);
