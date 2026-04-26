// =============================================================================
// Shared Authentication Helpers
// =============================================================================
// THIS FILE IS THE SOURCE OF TRUTH
// Copied to platform directories by scripts/prepare-frontend.sh
//
// Requires: platform-config.js, firebase app/auth/app-check compat SDKs
// =============================================================================

let appCheckActivated = false;

async function getHydratedCurrentUser() {
  const auth = firebase.auth();
  if (auth.currentUser) {
    return auth.currentUser;
  }

  return await new Promise((resolve) => {
    let done = false;
    const timeoutId = setTimeout(() => {
      if (done) return;
      done = true;
      unsubscribe();
      resolve(auth.currentUser);
    }, 3000);

    const unsubscribe = auth.onAuthStateChanged((user) => {
      if (done) return;
      done = true;
      clearTimeout(timeoutId);
      unsubscribe();
      resolve(user);
    });
  });
}

function buildGoogleProvider({ forceAccountChooser = false } = {}) {
  const provider = new firebase.auth.GoogleAuthProvider();
  const customParameters = {};
  if (PlatformConfig.workspaceDomain) {
    customParameters.hd = PlatformConfig.workspaceDomain;
  }
  if (forceAccountChooser) {
    customParameters.prompt = "select_account";
  }
  if (Object.keys(customParameters).length > 0) {
    provider.setCustomParameters(customParameters);
  }
  return provider;
}

async function signInWithGoogle({ forceAccountChooser = false } = {}) {
  const provider = buildGoogleProvider({ forceAccountChooser });
  try {
    const result = await firebase.auth().signInWithPopup(provider);
    return result.user;
  } catch (error) {
    if (
      error?.code === "auth/popup-blocked" ||
      error?.code === "auth/web-storage-unsupported"
    ) {
      await firebase.auth().signInWithRedirect(provider);
      return null;
    }
    throw error;
  }
}

function initializeFirebase(config) {
  if (!firebase.apps.length) {
    firebase.initializeApp(config);
  }

  if (!appCheckActivated && PlatformConfig.recaptchaSiteKey && firebase.appCheck) {
    firebase.appCheck().activate(
      new firebase.appCheck.ReCaptchaEnterpriseProvider(
        PlatformConfig.recaptchaSiteKey
      ),
      true
    );
    appCheckActivated = true;
  }
}

async function ensureSignedIn(options = {}) {
  const { interactive = false } = options;
  const auth = firebase.auth();
  const hydratedUser = await getHydratedCurrentUser();
  if (hydratedUser) return hydratedUser;

  if (PlatformConfig.requireSso) {
    if (!interactive) {
      throw new Error("SIGN_IN_REQUIRED");
    }
    return await signInWithGoogle({ forceAccountChooser: true });
  }

  const result = await auth.signInAnonymously();
  return result.user;
}

async function authHeaders(extraHeaders = {}) {
  const user = await ensureSignedIn();
  const idToken = await user.getIdToken(true);
  const headers = {
    ...extraHeaders,
    Authorization: `Bearer ${idToken}`,
  };

  if (PlatformConfig.recaptchaSiteKey && firebase.appCheck) {
    const appCheckToken = await firebase.appCheck().getToken();
    if (appCheckToken?.token) {
      headers["X-Firebase-AppCheck"] = appCheckToken.token;
    }
  }

  return headers;
}

function showSignInRequired(message, onSuccess) {
  const root = document.createElement("div");
  root.style.cssText =
    "position:fixed;inset:0;background:rgba(15,23,42,0.7);display:flex;align-items:center;justify-content:center;z-index:10000;padding:16px;";

  const card = document.createElement("div");
  card.style.cssText =
    "width:min(480px,100%);background:#fff;border-radius:12px;padding:24px;box-shadow:0 20px 30px rgba(0,0,0,0.2);text-align:center;";

  const title = document.createElement("h2");
  title.textContent = "Sign in required";
  title.style.marginBottom = "12px";

  const text = document.createElement("p");
  text.textContent = message || "Please sign in with Google to continue.";
  text.style.marginBottom = "16px";

  const error = document.createElement("p");
  error.style.cssText = "display:none;color:#b91c1c;margin-bottom:12px;";

  const button = document.createElement("button");
  button.textContent = "Sign in with Google";
  button.style.cssText =
    "padding:10px 16px;border:none;border-radius:8px;background:#2563eb;color:#fff;font-weight:600;cursor:pointer;";

  button.addEventListener("click", async () => {
    button.disabled = true;
    error.style.display = "none";
    try {
      const user = await ensureSignedIn({ interactive: true });
      if (!user) {
        // Redirect flow has started; browser navigation should happen shortly.
        return;
      }
      root.remove();
      if (typeof onSuccess === "function") {
        onSuccess();
      }
    } catch (err) {
      if (err?.code === "auth/unauthorized-domain") {
        error.textContent =
          "Sign-in failed because this site URL is not authorized for OAuth in Firebase. Open the app from its official web.app URL and try again.";
      } else {
        const details = err?.message ? ` (${err.message})` : "";
        error.textContent = `Sign-in was not completed. Please try again.${details}`;
      }
      error.style.display = "block";
      button.disabled = false;
    }
  });

  card.appendChild(title);
  card.appendChild(text);
  card.appendChild(error);
  card.appendChild(button);
  root.appendChild(card);
  document.body.appendChild(root);
}

function initSignOutButton() {
  const signOutButton = document.getElementById("signOutButton");
  if (!signOutButton) return;

  signOutButton.style.display = PlatformConfig.requireSso ? "inline-flex" : "none";
  signOutButton.addEventListener("click", async () => {
    try {
      await firebase.auth().signOut();
    } finally {
      window.location.href = "index.html";
    }
  });
}
