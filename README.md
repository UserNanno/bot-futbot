# bot-futbot

# INSTALACION

# 1.
git clone https://github.com/tuusuario/bot-futbot.git
cd bot-futbot

# 1.1
py -3.10 -m venv venv

# 2.
python -m venv venv

# 3. Activar entorno
# En Windows:
venv\Scripts\activate
# En macOS/Linux:
source venv/bin/activate

# 4.
pip install -r requirements.txt

# 5.
cp .env.example .env

# 5.5
rasa train --fixed-model-name futbot

# 6. Terminal (1)
rasa run actions --port 5055

# 7. Terminal (2)
rasa run --enable-api --cors "*" --model models/futbot.tar.gz

# ubuntu-terminal
source venv/bin/activate
sudo systemctl restart rasa-server
sudo systemctl status rasa-server --no-pager
tail -n 50 /var/log/rasa-server.log
