import { Amplify } from "aws-amplify";

// Public values only (CloudFormation outputs UserPoolId / UserPoolClientId).
// The app client has no secret, so nothing here is sensitive.
const userPoolId = import.meta.env.VITE_COGNITO_USER_POOL_ID;
const userPoolClientId = import.meta.env.VITE_COGNITO_CLIENT_ID;

export const cognitoConfigured = Boolean(userPoolId && userPoolClientId);

// Unconfigured, the app still renders; Login shows a "not configured" message.
if (cognitoConfigured) {
  Amplify.configure({
    Auth: {
      Cognito: {
        userPoolId,
        userPoolClientId,
        loginWith: { email: true },
        signUpVerificationMethod: "code",
      },
    },
  });
}
