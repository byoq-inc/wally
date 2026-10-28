import { createHmac } from "node:crypto";
import { verifyGithubWebhookSignature } from "./pr-review-webhook-signature";

const secret = "test-secret";
const rawBody = JSON.stringify({ hello: "world" });
const signatureHeader = `sha256=${createHmac("sha256", secret).update(rawBody, "utf8").digest("hex")}`;

console.log(
  JSON.stringify(
    {
      valid: verifyGithubWebhookSignature({ rawBody, signatureHeader, secret }),
      invalid: verifyGithubWebhookSignature({ rawBody, signatureHeader: "sha256=deadbeef", secret }),
    },
    null,
    2,
  ),
);
