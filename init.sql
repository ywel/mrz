CREATE DATABASE IF NOT EXISTS mrzdb;
USE mrzdb;
CREATE TABLE IF NOT EXISTS registrations (
    id INT AUTO_INCREMENT PRIMARY KEY,
    fullName VARCHAR(255),
    email VARCHAR(255),
    mobileNumber VARCHAR(20),
    areaOfResidence VARCHAR(255),
    emergencyContactName VARCHAR(255),
    relationship VARCHAR(100),
    emergencyContactMobileNumber VARCHAR(20)
);