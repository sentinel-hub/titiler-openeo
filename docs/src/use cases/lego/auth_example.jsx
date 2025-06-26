/**
 * Example React authentication helper for Lego Tile Services
 * 
 * This module provides authentication utilities for interacting with the Lego Tile Services
 * using the Copernicus Data Space Ecosystem (CDSE) OpenID Connect provider.
 */

// OIDC Configuration
const OIDC_CONFIG_URL = 'https://identity.dataspace.copernicus.eu/auth/realms/CDSE/.well-known/openid-configuration';
const CLIENT_ID = 'cdse-public';

/**
 * Generate a random string for PKCE code verifier
 */
function generateCodeVerifier() {
  const array = new Uint8Array(32);
  window.crypto.getRandomValues(array);
  return base64URLEncode(array);
}

/**
 * Create PKCE code challenge from verifier
 */
async function generateCodeChallenge(codeVerifier) {
  const encoder = new TextEncoder();
  const data = encoder.encode(codeVerifier);
  const digest = await window.crypto.subtle.digest('SHA-256', data);
  return base64URLEncode(new Uint8Array(digest));
}

/**
 * Base64URL encode an array
 */
function base64URLEncode(array) {
  return btoa(String.fromCharCode.apply(null, array))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
}

/**
 * Fetch OIDC configuration
 */
async function getOIDCConfig() {
  const response = await fetch(OIDC_CONFIG_URL);
  return response.json();
}

/**
 * Initialize OIDC authentication flow
 */
export async function initiateAuth() {
  // Generate PKCE values
  const codeVerifier = generateCodeVerifier();
  const codeChallenge = await generateCodeChallenge(codeVerifier);
  
  // Store code verifier for token exchange
  sessionStorage.setItem('code_verifier', codeVerifier);
  
  // Get OIDC configuration
  const config = await getOIDCConfig();
  
  // Generate random state
  const state = generateCodeVerifier();
  sessionStorage.setItem('auth_state', state);
  
  // Build authorization URL
  const authUrl = new URL(config.authorization_endpoint);
  authUrl.searchParams.append('client_id', CLIENT_ID);
  authUrl.searchParams.append('response_type', 'code');
  authUrl.searchParams.append('scope', 'openid email profile offline_access');
  authUrl.searchParams.append('redirect_uri', window.location.origin + '/auth-callback');
  authUrl.searchParams.append('code_challenge', codeChallenge);
  authUrl.searchParams.append('code_challenge_method', 'S256');
  authUrl.searchParams.append('state', state);
  
  // Redirect to authorization URL
  window.location.href = authUrl.toString();
}

/**
 * Handle authentication callback
 */
export async function handleAuthCallback() {
  const params = new URLSearchParams(window.location.search);
  const code = params.get('code');
  const state = params.get('state');
  const storedState = sessionStorage.getItem('auth_state');
  
  // Verify state
  if (state !== storedState) {
    throw new Error('Invalid state parameter');
  }
  
  // Get code verifier
  const codeVerifier = sessionStorage.getItem('code_verifier');
  
  // Get OIDC configuration
  const config = await getOIDCConfig();
  
  // Exchange code for tokens
  const tokens = await exchangeCode(code, codeVerifier, config.token_endpoint);
  
  // Store tokens
  storeTokens(tokens);
  
  return tokens;
}

/**
 * Exchange authorization code for tokens
 */
async function exchangeCode(code, codeVerifier, tokenEndpoint) {
  const response = await fetch(tokenEndpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded'
    },
    body: new URLSearchParams({
      grant_type: 'authorization_code',
      client_id: CLIENT_ID,
      code_verifier: codeVerifier,
      code: code,
      redirect_uri: window.location.origin + '/auth-callback'
    })
  });
  
  if (!response.ok) {
    throw new Error('Token exchange failed');
  }
  
  return response.json();
}

/**
 * Store tokens in session storage
 */
function storeTokens(tokens) {
  sessionStorage.setItem('access_token', tokens.access_token);
  if (tokens.refresh_token) {
    sessionStorage.setItem('refresh_token', tokens.refresh_token);
  }
  sessionStorage.setItem('token_expiry', Date.now() + (tokens.expires_in * 1000));
}

/**
 * Renew access token using refresh token
 */
async function renewToken() {
  const refreshToken = sessionStorage.getItem('refresh_token');
  if (!refreshToken) {
    throw new Error('No refresh token available');
  }
  
  const config = await getOIDCConfig();
  
  const response = await fetch(config.token_endpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded'
    },
    body: new URLSearchParams({
      grant_type: 'refresh_token',
      client_id: CLIENT_ID,
      refresh_token: refreshToken
    })
  });
  
  if (!response.ok) {
    // If renewal fails, clear tokens and redirect to login
    sessionStorage.clear();
    throw new Error('Token renewal failed');
  }
  
  const tokens = await response.json();
  storeTokens(tokens);
  return tokens;
}

/**
 * Get valid access token, renewing if necessary
 */
export async function getValidAccessToken() {
  const expiry = sessionStorage.getItem('token_expiry');
  const accessToken = sessionStorage.getItem('access_token');
  
  // Check if token is expired or will expire soon (within 5 minutes)
  if (!expiry || !accessToken || Date.now() > (Number(expiry) - 300000)) {
    try {
      const tokens = await renewToken();
      return tokens.access_token;
    } catch (error) {
      console.error('Token renewal failed:', error);
      return null;
    }
  }
  
  return accessToken;
}

/**
 * Format token for API requests
 */
export async function getAuthHeader() {
  const token = await getValidAccessToken();
  if (!token) return null;
  return {
    'Authorization': `Bearer oidc/oidc/${token}`
  };
}

/**
 * Example API call using authentication
 */
export async function callTileService(serviceId, zoom, x, y) {
  const headers = await getAuthHeader();
  if (!headers) {
    throw new Error('Not authenticated');
  }
  
  const response = await fetch(
    `/services/xyz/${serviceId}/tiles/${zoom}/${x}/${y}`,
    { headers }
  );
  
  if (!response.ok) {
    throw new Error(`API error: ${response.status}`);
  }
  
  return response;
}

/**
 * React hook for authentication
 */
export function useAuth() {
  const [isAuthenticated, setIsAuthenticated] = React.useState(false);
  const [isLoading, setIsLoading] = React.useState(true);
  
  // Check authentication status on mount
  React.useEffect(() => {
    async function checkAuth() {
      try {
        const token = await getValidAccessToken();
        setIsAuthenticated(!!token);
      } catch (error) {
        console.error('Auth check failed:', error);
        setIsAuthenticated(false);
      } finally {
        setIsLoading(false);
      }
    }
    checkAuth();
  }, []);
  
  const login = React.useCallback(() => {
    initiateAuth();
  }, []);
  
  const logout = React.useCallback(() => {
    sessionStorage.clear();
    setIsAuthenticated(false);
  }, []);
  
  return {
    isAuthenticated,
    isLoading,
    login,
    logout
  };
}

/**
 * Example usage in a React component:
 *
 * function App() {
 *   const { isAuthenticated, isLoading, login, logout } = useAuth();
 *
 *   if (isLoading) {
 *     return <div>Loading...</div>;
 *   }
 *
 *   return (
 *     <div>
 *       {isAuthenticated ? (
 *         <button onClick={logout}>Logout</button>
 *       ) : (
 *         <button onClick={login}>Login with CDSE</button>
 *       )}
 *     </div>
 *   );
 * }
 */
