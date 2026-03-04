/**
 * LoginPage — Shown when the user is not signed in.
 *
 * Displays a centered, company-branded page with a Google Sign-In button.
 * On successful sign-in, AuthContext.signIn() is called and this page
 * is replaced by the main VoiceApp.
 */

import { GoogleLogin } from "@react-oauth/google";
import { useAuth } from "../contexts/AuthContext";
import "./LoginPage.scss";

export function LoginPage() {
  const { signIn } = useAuth();

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-card__logo">
          {/* Swap this for your company logo */}
          <div className="login-card__logo-text">VoiceKit</div>
        </div>
        <h1 className="login-card__title">Sign in to your workspace</h1>
        <p className="login-card__subtitle">
          Use your company Google account to continue
        </p>
        <div className="login-card__btn-wrapper">
          <GoogleLogin
            onSuccess={signIn}
            onError={() => {
              console.error("[LoginPage] Google Sign-In failed");
            }}
            useOneTap={false}
            size="large"
            theme="outline"
            shape="rectangular"
            text="signin_with"
          />
        </div>
        <p className="login-card__footer">
          Only company Google accounts are allowed.
        </p>
      </div>
    </div>
  );
}
