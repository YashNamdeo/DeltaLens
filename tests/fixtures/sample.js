import { Router } from 'express';
import { UserModel } from './models/user';

class UserController {
    constructor(model) {
        this.model = model;
    }

    async getUser(req, res) {
        const user = await this.model.findById(req.params.id);
        res.json(user);
    }

    async createUser(req, res) {
        const user = await this.model.create(req.body);
        res.status(201).json(user);
    }
}

const validateEmail = (email) => {
    return email.includes('@');
};

function setupRoutes(app) {
    const controller = new UserController(UserModel);
    app.get('/users/:id', (req, res) => controller.getUser(req, res));
    app.post('/users', (req, res) => controller.createUser(req, res));
}

export default setupRoutes;
