function normalFunction(x, y) {
  return x + y;
}

const login = async (req, res) => {
  console.log("login");
}

const getUsers = (req, res) => {
  return [];
}

export const createUser = async (username) => {
  return { id: 1, username };
}

const App = () => <div />

class UserController {
  async index(req, res) {
    return getUsers(req, res);
  }
}
