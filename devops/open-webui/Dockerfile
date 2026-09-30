# Open WebUI plus email verification for new signups (see email_verification.py).
ARG OPEN_WEBUI_IMAGE=ghcr.io/open-webui/open-webui:main
FROM ${OPEN_WEBUI_IMAGE}

COPY devops/open-webui/email_verification.py /app/backend/email_verification.py

# Launch the wrapper app instead of open_webui.main:app; fail the build if start.sh changed shape.
RUN grep -q 'open_webui.main:app' /app/backend/start.sh \
 && sed -i 's/open_webui\.main:app/email_verification:app/g' /app/backend/start.sh
