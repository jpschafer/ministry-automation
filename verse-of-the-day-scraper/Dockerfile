FROM node:22-alpine
RUN mkdir -p /home/node/app/node_modules && chown -R node:node /home/node/app
WORKDIR /home/node/app
COPY --chown=node:node package*.json ./

RUN apk add dumb-init

USER node
RUN npm install
COPY --chown=node:node . .
RUN chmod +x ./entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/home/node/app/entrypoint.sh"]
