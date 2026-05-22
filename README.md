## FLask app
La aplicación define un servidor web usando Flask y organiza tanto el backend como los archivos visuales del frontend desde un único punto de entrada (`server.py`).
---

# Componentes definidos

## 1. Configuración inicial

```python
PORT  = 5000
HOST  = "127.0.0.1"
DEBUG = False
```

Se define:

- el puerto donde corre el servidor,
- la dirección local,
- y el modo debug.


## 2. Creación de la aplicación Flask

Aquí se crea la aplicación principal Flask.

También se configura:

- dónde están los archivos estáticos,
- y bajo qué URL se exponen.

Ejemplo:

```txt
/static/style.css
```

---

## 3. Registro de Blueprint

Se importa y registra un Blueprint.

Un Blueprint en Flask permite separar rutas y lógica del backend en módulos independientes.

 `controller/routes.py` contiene endpoints como:

```python
/api/consultar
/api/exportar
```

Esto ayuda a mantener una arquitectura más organizada.

---

## 4. Ruta principal `/`

```python
@app.route("/")
def index():
    return send_from_directory(VIEW_DIR, "index.html")
```

Define la ruta principal del sistema.

Cuando el usuario entra a:

```txt
http://127.0.0.1:5000
```

Flask devuelve:

```txt
view/index.html
```

Ese archivo contiene la interfaz principal de la aplicación.

---

# Arquitectura general

La estructura sigue una organización similar a MVC:

| Componente | Rol |
|---|---|
| `server.py` | Punto de entrada |
| `controller/` | Lógica y rutas |
| `view/` | Interfaz HTML/CSS/JS |
| Flask | Backend y servidor |

---

# Flujo de ejecución

1. Se ejecuta:

```bash
python server.py
```

2. Flask inicia el servidor.

3. Se registran las rutas API.

4. Se sirven archivos HTML, CSS y JS.

5. Se abre automáticamente el navegador.

6. El frontend consume los endpoints definidos en `controller.routes`.

---
### views
#### base
<img width="1364" height="648" alt="WhatsApp Image 2026-05-21 at 10 53 43 PM" src="https://github.com/user-attachments/assets/c2516e25-d938-448d-9cb7-4ea1b22496cb" />
#### archivo añadido
<img width="1267" height="644" alt="image" src="https://github.com/user-attachments/assets/b839c75c-d41b-499a-b794-e208b1904de8" />
#### consulta
<img width="1363" height="643" alt="image" src="https://github.com/user-attachments/assets/790811f2-7d81-4b34-ab63-d3fe55727130" />
#### exportador
<img width="1366" height="648" alt="image" src="https://github.com/user-attachments/assets/e50cbdad-dd37-4695-9976-5cbdd94f7014" />


### AL iniciar el proyecto
 1. Crear entorno virtual
`python -m venv venv`

 2. Activar entorno virtual

     #### Windows CMD
      `venv\Scripts\activate`
    
     #### Windows PowerShell
    `venv\Scripts\Activate.ps1`
    
     #### Linux / macOS
    `source venv/bin/activate`

 3. Instalar dependencias
`pip install -r requirements.txt`

4. Crear archivo .env en la raiz con key generada desde 2captcha
`2CAPTCHA_API_KEY=key_aqui`
5. instala playwright para ejecutar los navegadores headless
   `playwright install`
