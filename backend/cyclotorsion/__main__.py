"""Allow `python -m cyclotorsion` to run the dev server."""

import uvicorn

from cyclotorsion.app import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080, reload=False)
