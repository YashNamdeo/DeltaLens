interface UserDTO {
    id: number;
    name: string;
    email: string;
}

type UserRole = 'admin' | 'user' | 'guest';

class UserService {
    private db: Database;

    constructor(db: Database) {
        this.db = db;
    }

    async getUser(id: number): Promise<UserDTO> {
        return this.db.query('SELECT * FROM users WHERE id = ?', [id]);
    }

    async createUser(data: Partial<UserDTO>): Promise<UserDTO> {
        return this.db.insert('users', data);
    }

    async deleteUser(id: number): Promise<boolean> {
        const user = await this.getUser(id);
        if (!user) return false;
        return this.db.delete('users', id);
    }
}

const formatUser = (user: UserDTO): string => {
    return `${user.name} (${user.email})`;
};

export { UserService, formatUser };
export type { UserDTO, UserRole };
